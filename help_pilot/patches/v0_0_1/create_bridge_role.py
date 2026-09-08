# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""Create the HP Bridge role on sites installed before the bridge existed.

Frappe auto-creates roles it finds in a doctype's permissions table. HP Bridge
appears in none -- that is the whole point of it -- so it has to be made here.
"""

import frappe

from help_pilot.install import create_roles


def execute():
	create_roles()
	frappe.db.commit()
