# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""Whitelisted endpoints used by the Help Pilot desk UI."""

import frappe
from frappe import _

from help_pilot.permissions import get_user_departments, is_system_admin


def _ticket_or_throw(ticket: str):
	doc = frappe.get_doc("HP Ticket", ticket)
	doc.check_permission("read")
	return doc


def _can_act_as_agent(department: str, user: str | None = None) -> bool:
	user = user or frappe.session.user
	return is_system_admin(user) or department in get_user_departments(user)


@frappe.whitelist()
def get_ticket_thread(ticket: str) -> list[dict]:
	"""Comments on a ticket, with internal notes stripped out for the requester."""
	doc = _ticket_or_throw(ticket)
	is_agent = _can_act_as_agent(doc.department)

	filters = {"ticket": ticket}
	if not is_agent:
		filters["is_internal_note"] = 0

	comments = frappe.get_all(
		"HP Ticket Comment",
		filters=filters,
		fields=["name", "comment", "comment_by", "is_internal_note", "creation"],
		order_by="creation asc",
	)

	names = {c.comment_by for c in comments}
	full_names = dict(
		frappe.get_all("User", filters={"name": ["in", list(names)]}, fields=["name", "full_name"], as_list=True)
	) if names else {}

	for comment in comments:
		comment["full_name"] = full_names.get(comment.comment_by) or comment.comment_by
		comment["is_requester"] = comment.comment_by == doc.raised_by

	return comments


@frappe.whitelist()
def add_comment(ticket: str, comment: str, is_internal_note: int = 0) -> dict:
	doc = frappe.get_doc(
		{
			"doctype": "HP Ticket Comment",
			"ticket": ticket,
			"comment": comment,
			"is_internal_note": int(is_internal_note or 0),
		}
	)
	doc.insert()
	return {"name": doc.name}


@frappe.whitelist()
def get_ticket_context(ticket: str) -> dict:
	"""What the current user is allowed to do on this ticket, for the form UI."""
	doc = _ticket_or_throw(ticket)
	return {
		"is_agent": _can_act_as_agent(doc.department),
		"is_requester": doc.raised_by == frappe.session.user,
		"status": doc.status,
	}


@frappe.whitelist()
def get_my_stats() -> dict:
	"""Personal ticket counts for the user dashboard."""
	user = frappe.session.user
	rows = frappe.get_all(
		"HP Ticket",
		filters={"raised_by": user},
		fields=["status", "count(name) as count"],
		group_by="status",
	)
	counts = {row.status: row.count for row in rows}
	return {
		"open": counts.get("Open", 0) + counts.get("Reopened", 0),
		"in_progress": counts.get("In Progress", 0),
		"resolved": counts.get("Resolved", 0),
		"closed": counts.get("Closed", 0),
		"total": sum(counts.values()),
	}


@frappe.whitelist()
def get_department_stats(department: str | None = None) -> dict:
	"""Queue counts for the department/admin dashboard."""
	user = frappe.session.user
	if not is_system_admin(user):
		allowed = get_user_departments(user)
		if not allowed:
			frappe.throw(_("You are not a member of any department."), frappe.PermissionError)
		if department and department not in allowed:
			frappe.throw(_("You do not have access to {0}.").format(department), frappe.PermissionError)
		departments = [department] if department else allowed
	else:
		departments = [department] if department else None

	filters = {"department": ["in", departments]} if departments else {}

	rows = frappe.get_all(
		"HP Ticket", filters=filters, fields=["status", "count(name) as count"], group_by="status"
	)
	counts = {row.status: row.count for row in rows}

	avg_resolution = frappe.get_all(
		"HP Ticket",
		filters={**filters, "resolution_time": [">", 0]},
		fields=["avg(resolution_time) as avg_time"],
	)
	avg_seconds = (avg_resolution[0].avg_time if avg_resolution else 0) or 0

	return {
		"open": counts.get("Open", 0) + counts.get("Reopened", 0),
		"in_progress": counts.get("In Progress", 0),
		"resolved": counts.get("Resolved", 0),
		"closed": counts.get("Closed", 0),
		"total": sum(counts.values()),
		"avg_resolution_hours": round(float(avg_seconds) / 3600, 1),
	}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def department_agent_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link field query: only members of the ticket's department."""
	department = (filters or {}).get("department")
	if not department:
		return []

	agents = frappe.get_all(
		"HP Department Member",
		filters={"parent": department, "parenttype": "HP Department"},
		pluck="user",
	)
	if not agents:
		return []

	return frappe.get_all(
		"User",
		filters={"name": ["in", agents], "enabled": 1},
		or_filters={"full_name": ["like", f"%{txt}%"], "name": ["like", f"%{txt}%"]} if txt else None,
		fields=["name", "full_name"],
		as_list=True,
		limit_start=start,
		limit_page_length=page_len,
	)
