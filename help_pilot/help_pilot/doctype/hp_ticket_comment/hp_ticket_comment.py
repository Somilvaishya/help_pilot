# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_url_to_form, now_datetime

from help_pilot.permissions import get_user_departments, is_system_admin


class HPTicketComment(Document):
	def before_insert(self):
		self.comment_by = frappe.session.user

	def validate(self):
		self.ticket_doc = frappe.get_doc("HP Ticket", self.ticket)
		self.validate_can_comment()
		self.validate_internal_note()

	def validate_can_comment(self):
		user = frappe.session.user
		if self.is_agent(user) or self.ticket_doc.raised_by == user:
			return

		frappe.throw(_("You are not allowed to comment on this ticket."), frappe.PermissionError)

	def validate_internal_note(self):
		if self.is_internal_note and not self.is_agent(frappe.session.user):
			frappe.throw(_("Only agents can add internal notes."), frappe.PermissionError)

	def is_agent(self, user: str) -> bool:
		if is_system_admin(user):
			return True
		return self.ticket_doc.department in get_user_departments(user)

	def after_insert(self):
		self.stamp_first_response()
		self.notify_participants()

	def stamp_first_response(self):
		"""The first agent reply on a ticket is its first response."""
		if self.is_internal_note:
			return

		ticket = self.ticket_doc
		if ticket.first_responded_on or ticket.raised_by == self.comment_by:
			return

		if not self.is_agent(self.comment_by):
			return

		frappe.db.set_value("HP Ticket", ticket.name, "first_responded_on", now_datetime())

	def notify_participants(self):
		ticket = self.ticket_doc
		recipients = set()

		if self.is_internal_note:
			department = frappe.get_cached_doc("HP Department", ticket.department)
			recipients.update(department.get_agents())
		else:
			recipients.add(ticket.raised_by)
			if ticket.assigned_agent:
				recipients.add(ticket.assigned_agent)

		recipients.discard(self.comment_by)
		recipients = {r for r in recipients if r and r != "Administrator"}

		for recipient in recipients:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": _("New reply on ticket {0}").format(ticket.name),
					"email_content": frappe.utils.strip_html(self.comment)[:500],
					"for_user": recipient,
					"type": "Alert",
					"document_type": "HP Ticket",
					"document_name": ticket.name,
					"from_user": self.comment_by,
					"link": get_url_to_form("HP Ticket", ticket.name),
				}
			).insert(ignore_permissions=True)
