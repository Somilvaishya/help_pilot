# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""Live desk alerts: a popup and a sound, the moment something happens.

This only reaches people signed in to *this* site, because a websocket does not
cross sites. Agents are on the hub, so they get it instantly. Requesters sit on
their own ERP site and are told by `help_pilot_client`, which polls.

Nobody is ever alerted about their own action -- `push` drops the session user
before sending, so replying to a ticket never pings you about your own reply.
"""

import frappe

EVENT = "help_pilot_activity"

# Registered in hooks.py against files Frappe already ships. Do not use its own
# names here: "chime" is commented out in Frappe's hooks, so it plays nothing.
SOUND_NEW = "hp_new"
SOUND_REPLY = "hp_reply"
SOUND_STATUS = "hp_status"
SOUND_URGENT = "hp_urgent"


def push(
	recipients,
	*,
	title: str,
	body: str = "",
	ticket: str | None = None,
	sound: str = SOUND_NEW,
	kind: str = "activity",
):
	"""Fire a desk popup for each recipient who is not the person acting."""
	targets = {
		user
		for user in (recipients or [])
		if user and user not in ("Administrator", "Guest", frappe.session.user)
	}

	if not targets:
		return

	payload = {
		"kind": kind,
		"title": title,
		"body": frappe.utils.strip_html(body or "")[:240],
		"ticket": ticket,
		"sound": sound,
		"route": f"/app/hp-ticket/{ticket}" if ticket else None,
	}

	for user in targets:
		# after_commit, so nothing pops for a transaction that then rolls back.
		frappe.publish_realtime(EVENT, payload, user=user, after_commit=True)
