# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""Hub-side API for tickets raised on other ERP sites.

Every ERP site authenticates as its own bridge account and relays tickets here.
Two rules hold the whole thing together:

1. A bridge account may only act for the one `HP Source Site` that names it.
2. It may only touch tickets that both originated from that site *and* belong to
   the email it is acting for.

That second rule is why a leaked `site_config.json` costs you the tickets of one
site's own users -- which that site's administrator could already see -- rather
than the whole company's ticket history.
"""

import base64

import frappe
from frappe import _
from frappe.utils import cint, get_url, now_datetime, strip_html, validate_email_address

from help_pilot.permissions import BRIDGE_ROLE, is_system_admin

MAX_SUBJECT = 200
MAX_DESCRIPTION = 20000
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

# What a requester is allowed to do to their own ticket from a client site.
REQUESTER_STATUSES = {"Closed", "Reopened"}


def _authorise(source_site: str):
	"""Return the HP Source Site this caller may act for, or raise."""
	user = frappe.session.user

	if user in ("Guest", None):
		frappe.throw(_("Authentication required."), frappe.PermissionError)

	if BRIDGE_ROLE not in set(frappe.get_roles(user)) and not is_system_admin(user):
		frappe.throw(_("This account is not a Help Pilot bridge."), frappe.PermissionError)

	if not source_site:
		frappe.throw(_("source_site is required."), frappe.ValidationError)

	site = frappe.db.get_value(
		"HP Source Site",
		source_site,
		["name", "is_active", "bridge_user", "default_department", "base_url"],
		as_dict=True,
	)

	if not site:
		frappe.throw(_("Unknown source site {0}.").format(source_site), frappe.PermissionError)

	if not site.is_active:
		frappe.throw(_("Source site {0} is not active.").format(source_site), frappe.PermissionError)

	# A system admin may drive any site (useful for support and tests); a bridge
	# account is pinned to the single site that names it.
	if not is_system_admin(user) and site.bridge_user != user:
		frappe.throw(
			_("{0} is not the bridge account for {1}.").format(user, source_site), frappe.PermissionError
		)

	return site


def _clean_email(email: str) -> str:
	if not email:
		frappe.throw(_("requester_email is required."), frappe.ValidationError)

	email = email.strip().lower()
	if not validate_email_address(email):
		frappe.throw(_("{0} is not a valid email address.").format(email), frappe.ValidationError)

	return email


def _ensure_requester(email: str, full_name: str | None = None) -> str:
	"""Find, or create, the hub-side identity for a person on another site.

	They are created as a Website User with no roles: the hub is a destination
	for their tickets, not somewhere they need to log in. Give them `HP User`
	later if you want them reading tickets on the hub directly.
	"""
	if frappe.db.exists("User", email):
		return email

	first_name = (full_name or email.split("@")[0]).strip()[:140]

	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": first_name,
			"user_type": "Website User",
			"send_welcome_email": 0,
			"enabled": 1,
		}
	).insert(ignore_permissions=True)

	_keep_as_website_user(user)

	return email


def _keep_as_website_user(user):
	"""Undo desk access handed out by other apps on this hub.

	Apps installed alongside Help Pilot hook `User` creation and grant their own
	role -- `suite` adds "Suite User", for one -- and any role with desk access
	flips the account to System User. Left alone, that quietly turns every
	employee in the company into a licensed desk user on the hub the first time
	they file a ticket, which is the opposite of what auto-provisioning is for.

	They never sign in here: they see their tickets through their own ERP site.
	Set `help_pilot_allow_desk_requesters` in site_config.json to keep whatever
	the other apps decided.
	"""
	if frappe.conf.get("help_pilot_allow_desk_requesters"):
		return

	user.reload()

	desk_roles = [
		row.role
		for row in user.roles
		if frappe.db.get_value("Role", row.role, "desk_access")
	]

	if not desk_roles and user.user_type == "Website User":
		return

	if desk_roles:
		frappe.db.delete("Has Role", {"parent": user.name, "role": ["in", desk_roles]})

	frappe.db.set_value("User", user.name, "user_type", "Website User", update_modified=False)
	frappe.clear_cache(user=user.name)


def _owned_ticket(site, requester_email: str, ticket: str):
	"""A ticket the caller is allowed to touch: same site, same requester."""
	doc = frappe.db.get_value(
		"HP Ticket",
		{"name": ticket, "source_site": site.name, "raised_by": requester_email},
		["name", "raised_by", "department"],
		as_dict=True,
	)

	if not doc:
		# Deliberately indistinguishable from "does not exist" -- a bridge must
		# not be able to probe for ticket ids belonging to other people.
		frappe.throw(_("Ticket not found."), frappe.PermissionError)

	return doc


# ----------------------------------------------------------------------
# Write
# ----------------------------------------------------------------------
@frappe.whitelist()
def create_ticket(
	source_site: str,
	requester_email: str,
	subject: str,
	description: str,
	department: str | None = None,
	priority: str = "Medium",
	requester_full_name: str | None = None,
	source_reference: str | None = None,
) -> dict:
	site = _authorise(source_site)
	requester_email = _clean_email(requester_email)

	if not subject or not subject.strip():
		frappe.throw(_("Subject is required."), frappe.ValidationError)

	if not description or not strip_html(description).strip():
		frappe.throw(_("Description is required."), frappe.ValidationError)

	# Idempotency: the client retries a failed delivery with the same reference,
	# so replaying it must return the original ticket instead of a duplicate.
	if source_reference:
		existing = frappe.db.get_value(
			"HP Ticket",
			{"source_site": site.name, "source_reference": source_reference},
			["name", "status"],
			as_dict=True,
		)
		if existing:
			return _ticket_payload(existing.name, duplicate=True)

	department = department or site.default_department
	if not department:
		frappe.throw(_("No department given, and {0} has no default.").format(site.name))

	_ensure_requester(requester_email, requester_full_name)

	ticket = frappe.get_doc(
		{
			"doctype": "HP Ticket",
			"subject": subject.strip()[:MAX_SUBJECT],
			"description": description[:MAX_DESCRIPTION],
			"department": department,
			"priority": priority if priority in ("Low", "Medium", "High", "Urgent") else "Medium",
			"raised_by": requester_email,
			"source_site": site.name,
			"source_reference": source_reference,
		}
	)
	ticket.insert(ignore_permissions=True)

	return _ticket_payload(ticket.name)


@frappe.whitelist()
def add_reply(source_site: str, requester_email: str, ticket: str, comment: str) -> dict:
	site = _authorise(source_site)
	requester_email = _clean_email(requester_email)
	_owned_ticket(site, requester_email, ticket)

	if not comment or not strip_html(comment).strip():
		frappe.throw(_("Comment is required."), frappe.ValidationError)

	doc = frappe.get_doc(
		{
			"doctype": "HP Ticket Comment",
			"ticket": ticket,
			"comment": comment[:MAX_DESCRIPTION],
			"comment_by": requester_email,
			"is_internal_note": 0,
		}
	)
	doc.insert(ignore_permissions=True)

	return {"name": doc.name}


@frappe.whitelist()
def set_status(source_site: str, requester_email: str, ticket: str, status: str) -> dict:
	"""Let a requester close or reopen their own ticket from their own site."""
	site = _authorise(source_site)
	requester_email = _clean_email(requester_email)
	_owned_ticket(site, requester_email, ticket)

	if status not in REQUESTER_STATUSES:
		frappe.throw(
			_("A requester may only close or reopen a ticket, not set it to {0}.").format(status),
			frappe.ValidationError,
		)

	doc = frappe.get_doc("HP Ticket", ticket)
	doc.status = status
	doc.save(ignore_permissions=True)

	return _ticket_payload(ticket)


@frappe.whitelist()
def attach_file(
	source_site: str, requester_email: str, ticket: str, file_name: str, content_base64: str
) -> dict:
	site = _authorise(source_site)
	requester_email = _clean_email(requester_email)
	_owned_ticket(site, requester_email, ticket)

	try:
		raw = base64.b64decode(content_base64, validate=True)
	except Exception:
		frappe.throw(_("Attachment is not valid base64."), frappe.ValidationError)

	if len(raw) > MAX_ATTACHMENT_BYTES:
		frappe.throw(
			_("Attachment is larger than {0} MB.").format(MAX_ATTACHMENT_BYTES // (1024 * 1024)),
			frappe.ValidationError,
		)

	doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": "HP Ticket",
			"attached_to_name": ticket,
			"content": raw,
			"is_private": 1,
		}
	)
	doc.insert(ignore_permissions=True)

	return {"name": doc.name, "file_url": doc.file_url}


# ----------------------------------------------------------------------
# Read
# ----------------------------------------------------------------------
@frappe.whitelist()
def get_departments(source_site: str) -> list[dict]:
	_authorise(source_site)
	return frappe.get_all(
		"HP Department",
		filters={"is_active": 1},
		fields=["name", "description"],
		order_by="name asc",
	)


@frappe.whitelist()
def get_my_tickets(
	source_site: str, requester_email: str, status: str | None = None, limit: int = 50
) -> list[dict]:
	site = _authorise(source_site)
	requester_email = _clean_email(requester_email)

	filters = {"source_site": site.name, "raised_by": requester_email}
	if status:
		filters["status"] = status

	tickets = frappe.get_all(
		"HP Ticket",
		filters=filters,
		fields=[
			"name",
			"subject",
			"department",
			"status",
			"priority",
			"opening_datetime",
			"resolution_date",
			"modified",
		],
		order_by="modified desc",
		limit_page_length=min(cint(limit) or 50, 200),
	)

	_attach_reply_summary(tickets)
	return tickets


def _attach_reply_summary(tickets: list[dict]):
	"""Add reply_count and last_reply_by, in one query rather than per ticket.

	The client site uses `last_reply_by` to avoid telling someone about their
	own reply -- without it, every message a requester sends bounces straight
	back at them as a notification.
	"""
	if not tickets:
		return

	rows = frappe.get_all(
		"HP Ticket Comment",
		filters={"ticket": ["in", [t["name"] for t in tickets]], "is_internal_note": 0},
		fields=["ticket", "comment_by", "creation"],
		order_by="creation asc",
	)

	summary: dict[str, dict] = {}
	for row in rows:
		entry = summary.setdefault(row.ticket, {"count": 0, "last_by": None})
		entry["count"] += 1
		entry["last_by"] = row.comment_by

	for ticket in tickets:
		entry = summary.get(ticket["name"], {"count": 0, "last_by": None})
		ticket["reply_count"] = entry["count"]
		ticket["last_reply_by"] = entry["last_by"]


@frappe.whitelist()
def get_ticket(source_site: str, requester_email: str, ticket: str) -> dict:
	site = _authorise(source_site)
	requester_email = _clean_email(requester_email)
	_owned_ticket(site, requester_email, ticket)

	doc = frappe.get_doc("HP Ticket", ticket)

	# Internal notes never leave the hub.
	comments = frappe.get_all(
		"HP Ticket Comment",
		filters={"ticket": ticket, "is_internal_note": 0},
		fields=["name", "comment", "comment_by", "creation"],
		order_by="creation asc",
	)

	authors = {c.comment_by for c in comments}
	full_names = (
		dict(
			frappe.get_all(
				"User", filters={"name": ["in", list(authors)]}, fields=["name", "full_name"], as_list=True
			)
		)
		if authors
		else {}
	)

	for comment in comments:
		comment["full_name"] = full_names.get(comment.comment_by) or comment.comment_by
		comment["is_mine"] = comment.comment_by == requester_email

	return {
		"name": doc.name,
		"subject": doc.subject,
		"description": doc.description,
		"department": doc.department,
		"status": doc.status,
		"priority": doc.priority,
		"opening_datetime": doc.opening_datetime,
		"resolution_date": doc.resolution_date,
		"resolution_details": doc.resolution_details if doc.status in ("Resolved", "Closed") else None,
		"can_close": doc.status == "Resolved",
		"can_reopen": doc.status in ("Resolved", "Closed"),
		"comments": comments,
	}


def _ticket_payload(name: str, duplicate: bool = False) -> dict:
	row = frappe.db.get_value(
		"HP Ticket", name, ["name", "subject", "status", "department", "priority"], as_dict=True
	)
	row["hub_url"] = get_url(f"/app/hp-ticket/{name}")
	row["duplicate"] = duplicate
	return row
