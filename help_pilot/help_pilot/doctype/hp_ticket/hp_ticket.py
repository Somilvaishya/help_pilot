# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, get_url_to_form, now_datetime, time_diff_in_seconds

from help_pilot.permissions import get_user_departments, is_system_admin

OPEN_STATUSES = ("Open", "In Progress", "Reopened")
CLOSED_STATUSES = ("Resolved", "Closed")

ALLOWED_TRANSITIONS = {
	"Open": {"In Progress", "Resolved", "Closed"},
	"In Progress": {"Open", "Resolved", "Closed"},
	"Resolved": {"Closed", "Reopened"},
	"Closed": {"Reopened"},
	"Reopened": {"In Progress", "Resolved", "Closed"},
}

# What the person who raised the ticket may do on their own, without agent rights.
REQUESTER_TRANSITIONS = {
	"Resolved": {"Closed", "Reopened"},
	"Closed": {"Reopened"},
}


class HPTicket(Document):
	def before_insert(self):
		# The field carries a `__user` default so the form can satisfy its own
		# mandatory check, but only a system admin may raise on someone else's
		# behalf -- everyone else is pinned to their session.
		if not self.raised_by or not is_system_admin():
			self.raised_by = frappe.session.user
		self.opening_datetime = now_datetime()
		self.status = "Open"
		self.reopen_count = 0

	def validate(self):
		self.validate_department_is_active()
		self.validate_status_transition()
		self.validate_assigned_agent()
		self.set_resolution_timestamps()

	def validate_department_is_active(self):
		if not frappe.db.get_value("HP Department", self.department, "is_active"):
			frappe.throw(_("Department {0} is not active.").format(frappe.bold(self.department)))

	def validate_status_transition(self):
		if self.is_new():
			return

		previous = self.get_doc_before_save()
		if not previous or previous.status == self.status:
			return

		allowed = ALLOWED_TRANSITIONS.get(previous.status, set())
		if self.status not in allowed:
			frappe.throw(
				_("A ticket cannot go from {0} to {1}.").format(
					frappe.bold(_(previous.status)), frappe.bold(_(self.status))
				),
				title=_("Invalid Status Change"),
			)

		if not self.user_can_act_as_agent():
			requester_allowed = REQUESTER_TRANSITIONS.get(previous.status, set())
			if self.status not in requester_allowed:
				frappe.throw(
					_("You can only close or reopen your own ticket. Changing it to {0} is up to the {1} team.").format(
						frappe.bold(_(self.status)), frappe.bold(self.department)
					),
					title=_("Not Permitted"),
				)

		if self.status == "Reopened":
			self.reopen_count = (self.reopen_count or 0) + 1
			self.resolution_date = None
			self.resolution_time = None
			self.closed_on = None

	def validate_assigned_agent(self):
		if not self.assigned_agent:
			return

		department = frappe.get_cached_doc("HP Department", self.department)
		if self.assigned_agent not in department.get_agents():
			frappe.throw(
				_("{0} is not a member of the {1} department.").format(
					frappe.bold(self.assigned_agent), frappe.bold(self.department)
				)
			)

	def set_resolution_timestamps(self):
		if self.status == "Resolved" and not self.resolution_date:
			self.resolution_date = now_datetime()

		if self.status == "Closed" and not self.closed_on:
			self.closed_on = now_datetime()
			if not self.resolution_date:
				self.resolution_date = self.closed_on

		if self.resolution_date and self.opening_datetime:
			self.resolution_time = time_diff_in_seconds(self.resolution_date, self.opening_datetime)

	def user_can_act_as_agent(self, user: str | None = None) -> bool:
		user = user or frappe.session.user
		if is_system_admin(user):
			return True
		return self.department in get_user_departments(user)

	def after_insert(self):
		self.notify_department_of_new_ticket()

	def on_update(self):
		previous = self.get_doc_before_save()
		if not previous:
			return

		if previous.status != self.status:
			self.notify_requester_of_status_change(previous.status)

		if previous.assigned_agent != self.assigned_agent and self.assigned_agent:
			self.notify_assigned_agent()

	# ------------------------------------------------------------------
	# Notifications
	# ------------------------------------------------------------------
	def notify_department_of_new_ticket(self):
		department = frappe.get_cached_doc("HP Department", self.department)
		recipients = set(department.get_agents())
		if department.department_head:
			recipients.add(department.department_head)
		recipients.discard(self.raised_by)

		self.send_notification(
			recipients,
			subject=_("New {0} ticket: {1}").format(self.department, self.subject),
			message=_("{0} raised a {1} priority ticket for {2}.").format(
				self.raised_by_name or self.raised_by, _(self.priority), self.department
			),
		)

	def notify_requester_of_status_change(self, previous_status: str):
		if self.raised_by == frappe.session.user:
			return

		self.send_notification(
			{self.raised_by},
			subject=_("Ticket {0} is now {1}").format(self.name, _(self.status)),
			message=_("Your ticket {0} moved from {1} to {2}.").format(
				self.subject, _(previous_status), _(self.status)
			),
		)

	def notify_assigned_agent(self):
		if self.assigned_agent == frappe.session.user:
			return

		self.send_notification(
			{self.assigned_agent},
			subject=_("Ticket {0} assigned to you").format(self.name),
			message=_("{0} — {1} priority, raised by {2}.").format(
				self.subject, _(self.priority), self.raised_by_name or self.raised_by
			),
		)

	def send_notification(self, recipients, subject: str, message: str):
		recipients = {r for r in recipients if r and r != "Administrator"}
		if not recipients:
			return

		for recipient in recipients:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": subject,
					"email_content": message,
					"for_user": recipient,
					"type": "Alert",
					"document_type": self.doctype,
					"document_name": self.name,
					"from_user": frappe.session.user,
					"link": get_url_to_form(self.doctype, self.name),
				}
			).insert(ignore_permissions=True)


def auto_close_resolved_tickets():
	"""Close Resolved tickets that the requester never reopened. Runs daily."""
	days = int(frappe.conf.get("hp_auto_close_days") or 7)

	tickets = frappe.get_all(
		"HP Ticket",
		filters={
			"status": "Resolved",
			"resolution_date": ["<", add_days(now_datetime(), -days)],
		},
		pluck="name",
	)

	for name in tickets:
		ticket = frappe.get_doc("HP Ticket", name)
		ticket.status = "Closed"
		ticket.save(ignore_permissions=True)

	if tickets:
		frappe.db.commit()
