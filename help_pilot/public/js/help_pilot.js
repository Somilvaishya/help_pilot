// Copyright (c) 2026, Somil Vaishya and contributors
// For license information, please see license.txt

// Live desk alerts for agents: a popup, a sound, and a desktop notification
// when the tab is in the background. The server never sends you your own
// action, so this cannot ping you about something you just did yourself.

frappe.provide("help_pilot");

help_pilot.SOUND_KEY = "help_pilot_sound_on";
help_pilot.recent = [];

help_pilot.sound_enabled = function () {
	try {
		return localStorage.getItem(help_pilot.SOUND_KEY) !== "0";
	} catch (e) {
		return true;
	}
};

help_pilot.set_sound = function (on) {
	try {
		localStorage.setItem(help_pilot.SOUND_KEY, on ? "1" : "0");
	} catch (e) {
		// Private window, or site data blocked. The toggle just will not stick.
	}
};

help_pilot.play = function (name) {
	if (!help_pilot.sound_enabled()) {
		return;
	}

	// play_sound looks up an <audio> element by id and throws if the sound was
	// never registered. Fall back to one Frappe always ships rather than go
	// silent, which is indistinguishable from the feature being broken.
	let sound = name || "hp_new";
	if (!$("#sound-" + sound).length) {
		sound = "alert";
	}

	try {
		frappe.utils.play_sound(sound);
	} catch (e) {
		// Browsers refuse audio before the first interaction with the page.
		// Silence is the right fallback, never an error.
	}
};

help_pilot.alert = function (data) {
	if (!data || !data.title) {
		return;
	}

	// The same event can arrive twice if two tabs share a session; dedupe on a
	// short window so the sound does not double up.
	const key = `${data.kind}:${data.ticket}:${data.title}`;
	const now = Date.now();
	help_pilot.recent = help_pilot.recent.filter((r) => now - r.at < 4000);
	if (help_pilot.recent.some((r) => r.key === key)) {
		return;
	}
	help_pilot.recent.push({ key, at: now });

	help_pilot.play(data.sound);
	help_pilot.toast(data);
	help_pilot.desktop(data);
};

help_pilot.toast = function (data) {
	const colours = {
		new_ticket: "orange",
		reply: "blue",
		internal_note: "yellow",
		status: "green",
		assigned: "blue",
	};

	const $msg = $(`
		<div class="hp-toast">
			<div class="hp-toast-title">${frappe.utils.escape_html(data.title)}</div>
			${data.body ? `<div class="hp-toast-body">${frappe.utils.escape_html(data.body)}</div>` : ""}
			${data.ticket ? `<div class="hp-toast-open">${__("Open")} &rarr;</div>` : ""}
		</div>
	`);

	if (data.route) {
		$msg.css("cursor", "pointer").on("click", () => {
			frappe.set_route(data.route.replace(/^\/app\//, "").split("/"));
		});
	}

	frappe.show_alert({ message: $msg.prop("outerHTML"), indicator: colours[data.kind] || "blue" }, 12);

	// show_alert re-renders the html, so bind the click on the live node.
	if (data.route) {
		$(".hp-toast")
			.last()
			.css("cursor", "pointer")
			.on("click", () => frappe.set_route(data.route.replace(/^\/app\//, "").split("/")));
	}
};

help_pilot.desktop = function (data) {
	// Only worth a desktop notification when they cannot see the tab.
	if (typeof Notification === "undefined" || !document.hidden) {
		return;
	}
	if (Notification.permission !== "granted") {
		return;
	}

	try {
		const n = new Notification(data.title, { body: data.body || "", tag: data.ticket || "help-pilot" });
		n.onclick = function () {
			window.focus();
			if (data.route) {
				frappe.set_route(data.route.replace(/^\/app\//, "").split("/"));
			}
			n.close();
		};
	} catch (e) {
		// Notification constructor throws on some mobile browsers.
	}
};

help_pilot.ask_desktop_permission = function () {
	if (typeof Notification === "undefined" || Notification.permission !== "default") {
		return;
	}

	// Asking cold is rude and usually gets denied forever. Ask once, on the
	// first click anywhere, so it reads as a response to the user being here.
	$(document).one("click", () => {
		try {
			Notification.requestPermission();
		} catch (e) {
			// Older signature, or blocked by policy.
		}
	});
};

help_pilot.since = null;
help_pilot.poll_timer = null;

// Realtime is instant but silently dead if the websocket never connects, and
// the user cannot tell: the bell count still moves. Poll as a backstop.
help_pilot.POLL_MS = 60000;

help_pilot.poll = function () {
	frappe.call({
		method: "help_pilot.api.poll_alerts",
		args: { since: help_pilot.since },
		freeze: false,
		callback: ({ message }) => {
			if (!message) {
				return;
			}
			help_pilot.since = message.now || help_pilot.since;
			(message.events || []).forEach(help_pilot.alert);
		},
		error: () => {},
	});
};

help_pilot.start = function () {
	if (help_pilot.started) {
		return;
	}
	help_pilot.started = true;

	frappe.realtime.on("help_pilot_activity", help_pilot.alert);
	help_pilot.ask_desktop_permission();

	help_pilot.poll();
	help_pilot.poll_timer = setInterval(help_pilot.poll, help_pilot.POLL_MS);
	$(document).on("visibilitychange", () => {
		if (!document.hidden) {
			help_pilot.poll();
		}
	});
};

// app_ready may already have fired by the time this bundle runs, in which case
// binding only to the event would never happen at all.
$(document).on("app_ready", help_pilot.start);
$(document).ready(() => {
	if (frappe.realtime && frappe.boot) {
		help_pilot.start();
	}
});
