# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""Row-level isolation for Help Pilot.

Role permissions decide *what kind* of thing a user may do; everything in this
module decides *which rows* they may do it to:

* HP User            -> only tickets they raised
* HP Agent / Admin   -> tickets of the departments they are a member of, plus
                        their own tickets raised against any other department
* HP System Admin    -> everything
"""

import frappe

SYSTEM_ADMIN_ROLES = {"HP System Admin", "Administrator", "System Manager"}
AGENT_ROLES = {"HP Agent", "HP Department Admin"}
BRIDGE_ROLE = "HP Bridge"

DEPARTMENT_CACHE_KEY = "help_pilot_user_departments"


def is_system_admin(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(SYSTEM_ADMIN_ROLES & set(frappe.get_roles(user)))


def is_agent(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return bool(AGENT_ROLES & set(frappe.get_roles(user)))


def can_raise_on_behalf(user: str | None = None) -> bool:
	"""May this account file a ticket, or reply, in someone else's name?

	Only two kinds of caller can: a system admin logging a phoned-in complaint,
	and a bridge account relaying a ticket from another ERP site. Neither path
	is reachable from the ticket form, which keeps `raised_by` read-only.
	"""
	user = user or frappe.session.user
	return is_system_admin(user) or BRIDGE_ROLE in set(frappe.get_roles(user))


def get_user_departments(user: str | None = None) -> list[str]:
	"""Departments this user is a member of, from the HP Department members table."""
	user = user or frappe.session.user

	def _fetch():
		return frappe.get_all(
			"HP Department Member",
			filters={"user": user, "parenttype": "HP Department"},
			pluck="parent",
			ignore_permissions=True,
		)

	return frappe.cache().hget(DEPARTMENT_CACHE_KEY, user, _fetch) or []


def get_department_admin_departments(user: str | None = None) -> list[str]:
	"""Departments where this user is specifically a Department Admin."""
	user = user or frappe.session.user
	return frappe.get_all(
		"HP Department Member",
		filters={"user": user, "member_role": "Department Admin", "parenttype": "HP Department"},
		pluck="parent",
		ignore_permissions=True,
	)


def clear_department_cache(user: str | None = None):
	if user:
		frappe.cache().hdel(DEPARTMENT_CACHE_KEY, user)
	else:
		frappe.cache().delete_key(DEPARTMENT_CACHE_KEY)


def on_department_member_change(doc, method=None):
	"""Hooked on User so a role change also drops the cache."""
	clear_department_cache(doc.name if doc.doctype == "User" else None)


# ----------------------------------------------------------------------
# HP Ticket
# ----------------------------------------------------------------------
def get_permission_query_conditions_for_ticket(user: str | None = None) -> str:
	user = user or frappe.session.user
	if is_system_admin(user):
		return ""

	escaped_user = frappe.db.escape(user)
	clauses = [f"`tabHP Ticket`.`raised_by` = {escaped_user}"]

	departments = get_user_departments(user)
	if departments:
		in_list = ", ".join(frappe.db.escape(d) for d in departments)
		clauses.append(f"`tabHP Ticket`.`department` in ({in_list})")

	return "({0})".format(" or ".join(clauses))


def has_permission_for_ticket(doc, ptype: str = "read", user: str | None = None) -> bool:
	user = user or frappe.session.user

	# Anyone with the role-level create right may raise a ticket; there is no row
	# to isolate yet, and `raised_by` is only stamped in before_insert.
	if ptype == "create":
		return True

	if is_system_admin(user):
		return True

	if doc.department in get_user_departments(user):
		return True

	# The requester keeps read/write on their own ticket (to comment or reopen),
	# but may never delete it.
	if (doc.raised_by or user) == user:
		return ptype in ("read", "write", "print", "email", "share", "submit")

	return False


# ----------------------------------------------------------------------
# HP Ticket Comment
# ----------------------------------------------------------------------
def get_permission_query_conditions_for_comment(user: str | None = None) -> str:
	user = user or frappe.session.user
	if is_system_admin(user):
		return ""

	escaped_user = frappe.db.escape(user)
	clauses = [
		"(`tabHP Ticket Comment`.`is_internal_note` = 0 and `tabHP Ticket Comment`.`ticket` in "
		f"(select `name` from `tabHP Ticket` where `raised_by` = {escaped_user}))"
	]

	departments = get_user_departments(user)
	if departments:
		in_list = ", ".join(frappe.db.escape(d) for d in departments)
		clauses.append(
			"`tabHP Ticket Comment`.`ticket` in "
			f"(select `name` from `tabHP Ticket` where `department` in ({in_list}))"
		)

	return "({0})".format(" or ".join(clauses))


def has_permission_for_comment(doc, ptype: str = "read", user: str | None = None) -> bool:
	user = user or frappe.session.user
	if is_system_admin(user):
		return True

	ticket = frappe.db.get_value(
		"HP Ticket", doc.ticket, ["department", "raised_by"], as_dict=True
	)
	if not ticket:
		return False

	if ticket.department in get_user_departments(user):
		return True

	if ticket.raised_by == user:
		# The requester never sees internal notes, and cannot edit a reply.
		return not doc.is_internal_note and ptype in ("read", "create", "print", "email")

	return False


# ----------------------------------------------------------------------
# HP Department
# ----------------------------------------------------------------------
def get_permission_query_conditions_for_department(user: str | None = None) -> str:
	"""Everyone can read the department list (they need it to raise a ticket),
	so this only hides inactive departments from non-admins."""
	user = user or frappe.session.user
	if is_system_admin(user):
		return ""

	return "`tabHP Department`.`is_active` = 1"
