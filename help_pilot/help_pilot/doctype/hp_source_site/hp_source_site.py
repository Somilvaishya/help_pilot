# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

BRIDGE_ROLE = "HP Bridge"


class HPSourceSite(Document):
	def validate(self):
		self.normalise_base_url()
		self.validate_bridge_user()

	def normalise_base_url(self):
		if self.base_url:
			self.base_url = self.base_url.rstrip("/")

	def validate_bridge_user(self):
		"""The bridge account must hold the bridge role and nothing that can read tickets.

		A site config file is a much softer target than the hub itself, so the
		credential it holds is only ever allowed to file tickets on behalf of a
		named person -- never to read the ticket table.
		"""
		roles = set(frappe.get_roles(self.bridge_user))

		if BRIDGE_ROLE not in roles:
			frappe.throw(
				_("{0} does not have the {1} role. Add it before using this account as a bridge.").format(
					frappe.bold(self.bridge_user), frappe.bold(BRIDGE_ROLE)
				)
			)

		risky = roles & {"HP Agent", "HP Department Admin", "HP System Admin", "System Manager"}
		if risky:
			frappe.throw(
				_("{0} also holds {1}. A bridge account must not be able to read tickets -- use a dedicated account.").format(
					frappe.bold(self.bridge_user), frappe.bold(", ".join(sorted(risky)))
				),
				title=_("Bridge Account Is Over-Privileged"),
			)

	def on_update(self):
		frappe.cache().delete_key("help_pilot_source_sites")

	def on_trash(self):
		if frappe.db.exists("HP Ticket", {"source_site": self.name}):
			frappe.throw(
				_("Tickets have already arrived from {0}. Mark it inactive instead of deleting it.").format(
					self.name
				)
			)
		frappe.cache().delete_key("help_pilot_source_sites")
