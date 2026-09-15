# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""Whitelisted endpoints used by the Help Pilot desk UI."""

import frappe
from frappe import _
from frappe.utils import now_datetime

from help_pilot import realtime
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
def transfer_ticket(
	ticket: str,
	department: str,
	issue_category: str | None = None,
	reason: str | None = None,
) -> dict:
	"""Hand a ticket to another department.

	Closing a ticket that belongs to someone else is the wrong answer: the
	requester has to start again and the history is lost. Moving it keeps the
	thread, the attachments and the age, and tells both sides what happened.

	Note the person doing this usually loses sight of the ticket the moment it
	lands, because they are not a member of the receiving department. That is
	the isolation working, so say so rather than leave them wondering.
	"""
	doc = frappe.get_doc("HP Ticket", ticket)
	previous = doc.department

	if not _can_act_as_agent(previous):
		frappe.throw(
			_("Only the {0} team can move this ticket.").format(previous), frappe.PermissionError
		)

	if department == previous:
		frappe.throw(_("The ticket is already with {0}.").format(department), frappe.ValidationError)

	target = frappe.db.get_value("HP Department", department, ["name", "is_active"], as_dict=True)
	if not target:
		frappe.throw(_("Department {0} does not exist.").format(department))
	if not target.is_active:
		frappe.throw(_("{0} is not active.").format(department))

	if not reason or not reason.strip():
		frappe.throw(_("Please say why it is moving. The next team needs it."))

	moved_by = frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user

	# Write both notes *before* the move. A comment is checked against the
	# ticket's current department, and the moment this ticket belongs to the
	# other team its old agent may no longer write on it -- not even to record
	# why they handed it over. Everything here shares one transaction, so a
	# failure below takes these with it.
	#
	# The reason is operational, so it stays between agents.
	frappe.get_doc(
		{
			"doctype": "HP Ticket Comment",
			"ticket": ticket,
			"comment": _("<p>Moved from <b>{0}</b> to <b>{1}</b> by {2}.</p><p>{3}</p>").format(
				previous, department, moved_by, frappe.utils.escape_html(reason.strip())
			),
			"is_internal_note": 1,
		}
	).insert(ignore_permissions=True)

	# The requester sees the department change anyway; better they hear it from
	# the ticket than notice it and wonder.
	frappe.get_doc(
		{
			"doctype": "HP Ticket Comment",
			"ticket": ticket,
			"comment": _("<p>This ticket has been moved to the <b>{0}</b> team.</p>").format(department),
			"is_internal_note": 0,
		}
	).insert(ignore_permissions=True)

	doc.reload()
	doc.department = department
	# Both belong to the department it is leaving.
	doc.assigned_agent = None
	doc.issue_category = issue_category or None
	doc.save(ignore_permissions=True)

	receiving = frappe.get_cached_doc("HP Department", department)
	recipients = set(receiving.get_agents())
	if receiving.department_head:
		recipients.add(receiving.department_head)

	realtime.push(
		recipients,
		title=_("{0} moved here: {1}").format(previous, doc.subject),
		body=reason.strip(),
		ticket=ticket,
		sound=realtime.SOUND_NEW,
		kind="new_ticket",
	)

	return {
		"name": ticket,
		"department": department,
		"still_visible": _can_act_as_agent(department),
	}


@frappe.whitelist()
def poll_alerts(since: str | None = None) -> dict:
	"""Help Pilot alerts raised since this browser last looked.

	The websocket is the primary path and is instant. This is the backstop for
	when it is not connected -- a failure that is otherwise invisible, because
	the bell count still moves and nothing else happens, so it reads as "the
	notification only arrives when I refresh".

	The first call carries no `since` and deliberately returns nothing: it just
	hands back the clock, so the browser has a starting point and does not
	replay everything already sitting unread.
	"""
	user = frappe.session.user
	now = str(now_datetime())

	if user in ("Guest", None) or not since:
		return {"events": [], "now": now}

	rows = frappe.get_all(
		"Notification Log",
		filters={
			"for_user": user,
			"read": 0,
			"document_type": "HP Ticket",
			"creation": [">", since],
		},
		fields=["subject", "email_content", "document_name", "creation"],
		order_by="creation asc",
		limit_page_length=10,
	)

	return {
		"events": [
			{
				"kind": "activity",
				"title": row.subject,
				"body": row.email_content,
				"ticket": row.document_name,
				"sound": realtime.SOUND_NEW,
				"route": f"/app/hp-ticket/{row.document_name}" if row.document_name else None,
			}
			for row in rows
		],
		"now": now,
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
