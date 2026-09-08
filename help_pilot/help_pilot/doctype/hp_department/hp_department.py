# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from help_pilot.permissions import clear_department_cache


class HPDepartment(Document):
	def validate(self):
		self.validate_duplicate_members()

	def validate_duplicate_members(self):
		seen = set()
		for row in self.members:
			if row.user in seen:
				frappe.throw(
					_("Row {0}: {1} is already a member of this department.").format(row.idx, row.user)
				)
			seen.add(row.user)

	def on_update(self):
		clear_department_cache()

	def on_trash(self):
		if frappe.db.exists("HP Ticket", {"department": self.name}):
			frappe.throw(
				_("Cannot delete {0} because tickets are already raised against it. Mark it inactive instead.").format(
					self.name
				)
			)
		clear_department_cache()

	def get_agents(self):
		"""Users who can act on tickets of this department."""
		return [row.user for row in self.members]

	def get_department_admins(self):
		return [row.user for row in self.members if row.member_role == "Department Admin"]
