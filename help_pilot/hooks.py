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
app_include_js = "/assets/help_pilot/js/help_pilot.js"

# Frappe ships these mp3 files but registers only some of them, and `chime` is
# commented out in its own hooks -- so play_sound("chime") finds no <audio>
# element and silently does nothing. Register our own names against the files
# that are definitely there, and a little louder: 0.1 is inaudible in an office.
sounds = [
	{"name": "hp_new", "src": "/assets/frappe/sounds/chime.mp3", "volume": 0.5},
	{"name": "hp_reply", "src": "/assets/frappe/sounds/email.mp3", "volume": 0.5},
	{"name": "hp_status", "src": "/assets/frappe/sounds/alert.mp3", "volume": 0.5},
	{"name": "hp_urgent", "src": "/assets/frappe/sounds/error.mp3", "volume": 0.6},
]


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
