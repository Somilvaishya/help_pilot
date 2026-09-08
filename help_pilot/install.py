# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

import frappe

ROLES = [
	{"role_name": "HP User", "desk_access": 1},
	{"role_name": "HP Agent", "desk_access": 1},
	{"role_name": "HP Department Admin", "desk_access": 1},
	{"role_name": "HP System Admin", "desk_access": 1},
	# API-only: relays tickets from other ERP sites. Deliberately has no desk
	# access and no read permission on any Help Pilot doctype.
	{"role_name": "HP Bridge", "desk_access": 0},
]


def after_install():
	create_roles()
	frappe.db.commit()


def create_roles():
	for role in ROLES:
		if frappe.db.exists("Role", role["role_name"]):
			continue

		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": role["role_name"],
				"desk_access": role["desk_access"],
				"is_custom": 1,
			}
		).insert(ignore_permissions=True)
