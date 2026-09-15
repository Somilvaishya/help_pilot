# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class HPIssueCategory(Document):
	def validate(self):
		self.category_name = (self.category_name or "").strip()

		if "-" in (self.category_name or "") and self.is_new():
			# The name is built as "{department} - {category_name}", so a hyphen
			# in the category itself makes the composite id ambiguous to read.
			frappe.msgprint(
				_("Avoid hyphens in a category name -- the record is named {0}.").format(
					frappe.bold(f"{self.department} - {self.category_name}")
				),
				indicator="orange",
				alert=True,
			)

	def on_update(self):
		frappe.cache().delete_key("help_pilot_categories")

	def on_trash(self):
		if frappe.db.exists("HP Ticket", {"issue_category": self.name}):
			frappe.throw(
				_("Tickets already use {0}. Mark it inactive instead of deleting it.").format(self.name)
			)
		frappe.cache().delete_key("help_pilot_categories")
