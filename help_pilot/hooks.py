app_name = "help_pilot"
app_title = "Help Pilot"
app_publisher = "Somil Vaishya"
app_description = "Organization-wide help ticket management for Frappe"
app_email = "somil@example.com"
app_license = "mit"

# ----------------------------------------------------------------------
# Includes
# ----------------------------------------------------------------------
app_include_css = "/assets/help_pilot/css/help_pilot.css"

# ----------------------------------------------------------------------
# Installation
# ----------------------------------------------------------------------
after_install = "help_pilot.install.after_install"

# ----------------------------------------------------------------------
# Row-level isolation
# ----------------------------------------------------------------------
permission_query_conditions = {
	"HP Ticket": "help_pilot.permissions.get_permission_query_conditions_for_ticket",
	"HP Ticket Comment": "help_pilot.permissions.get_permission_query_conditions_for_comment",
	"HP Department": "help_pilot.permissions.get_permission_query_conditions_for_department",
}

has_permission = {
	"HP Ticket": "help_pilot.permissions.has_permission_for_ticket",
	"HP Ticket Comment": "help_pilot.permissions.has_permission_for_comment",
}

# ----------------------------------------------------------------------
# Document events
# ----------------------------------------------------------------------
doc_events = {
	"User": {
		"on_update": "help_pilot.permissions.on_department_member_change",
	},
}

# ----------------------------------------------------------------------
# Scheduled tasks
# ----------------------------------------------------------------------
scheduler_events = {
	"daily": [
		"help_pilot.help_pilot.doctype.hp_ticket.hp_ticket.auto_close_resolved_tickets",
	],
}
