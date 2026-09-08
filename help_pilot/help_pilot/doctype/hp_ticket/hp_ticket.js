// Copyright (c) 2026, Somil Vaishya and contributors
// For license information, please see license.txt

frappe.ui.form.on("HP Ticket", {
	setup(frm) {
		frm.set_query("assigned_agent", () => ({
			query: "help_pilot.api.department_agent_query",
			filters: { department: frm.doc.department },
		}));
	},

	refresh(frm) {
		frm.set_df_property("department", "read_only", !frm.is_new());

		if (frm.is_new()) {
			return;
		}

		frappe.call({
			method: "help_pilot.api.get_ticket_context",
			args: { ticket: frm.doc.name },
			callback: ({ message }) => {
				frm.hp_context = message || {};
				render_status_actions(frm);
				render_thread(frm);
			},
		});
	},

	department(frm) {
		if (frm.doc.assigned_agent) {
			frm.set_value("assigned_agent", null);
		}
	},
});

function render_status_actions(frm) {
	const { is_agent, is_requester } = frm.hp_context;
	const status = frm.doc.status;

	const transitions = [];

	if (is_agent) {
		if (["Open", "Reopened"].includes(status)) {
			transitions.push(["Start Working", "In Progress"]);
		}
		if (["Open", "In Progress", "Reopened"].includes(status)) {
			transitions.push(["Mark Resolved", "Resolved"]);
		}
		if (status !== "Closed") {
			transitions.push(["Close", "Closed"]);
		}
	}

	if (is_requester && !is_agent) {
		if (status === "Resolved") {
			transitions.push(["Confirm & Close", "Closed"]);
			transitions.push(["Reopen", "Reopened"]);
		}
		if (status === "Closed") {
			transitions.push(["Reopen", "Reopened"]);
		}
	}

	transitions.forEach(([label, target]) => {
		frm.add_custom_button(__(label), () => set_status(frm, target), __("Status"));
	});

	if (is_agent && !frm.doc.assigned_agent) {
		frm.add_custom_button(__("Assign to Me"), () => {
			frm.set_value("assigned_agent", frappe.session.user).then(() => frm.save());
		});
	}
}

function set_status(frm, status) {
	if (status === "Resolved" && !frm.doc.resolution_details) {
		frappe.prompt(
			{
				fieldname: "resolution_details",
				fieldtype: "Text Editor",
				label: __("How was this resolved?"),
				reqd: 1,
			},
			({ resolution_details }) => {
				frm.set_value("resolution_details", resolution_details);
				frm.set_value("status", status).then(() => frm.save());
			},
			__("Resolve Ticket"),
			__("Resolve")
		);
		return;
	}

	frm.set_value("status", status).then(() => frm.save());
}

function render_thread(frm) {
	const wrapper = frm.dashboard.add_section("", __("Conversation"));
	wrapper.empty();

	const $thread = $('<div class="hp-thread"></div>').appendTo(wrapper);
	const $actions = $('<div class="hp-thread-actions"></div>').appendTo(wrapper);

	$('<button class="btn btn-primary btn-sm">' + __("Add Reply") + "</button>")
		.appendTo($actions)
		.on("click", () => open_reply_dialog(frm, 0));

	if (frm.hp_context.is_agent) {
		$('<button class="btn btn-default btn-sm ml-2">' + __("Internal Note") + "</button>")
			.appendTo($actions)
			.on("click", () => open_reply_dialog(frm, 1));
	}

	frappe.call({
		method: "help_pilot.api.get_ticket_thread",
		args: { ticket: frm.doc.name },
		callback: ({ message }) => {
			if (!message || !message.length) {
				$thread.html(
					'<div class="hp-thread-empty text-muted">' + __("No replies yet.") + "</div>"
				);
				return;
			}

			message.forEach((comment) => $thread.append(comment_html(comment)));
		},
	});
}

function comment_html(comment) {
	const side = comment.is_requester ? "requester" : "agent";
	const note = comment.is_internal_note ? " hp-internal" : "";
	const badge = comment.is_internal_note
		? '<span class="hp-badge">' + __("Internal Note") + "</span>"
		: "";

	return $(`
		<div class="hp-comment hp-${side}${note}">
			<div class="hp-comment-head">
				<span class="hp-author">${frappe.utils.escape_html(comment.full_name)}</span>
				${badge}
				<span class="hp-time">${frappe.datetime.comment_when(comment.creation)}</span>
			</div>
			<div class="hp-comment-body">${frappe.dom.remove_script_and_style(comment.comment)}</div>
		</div>
	`);
}

function open_reply_dialog(frm, is_internal_note) {
	frappe.prompt(
		{
			fieldname: "comment",
			fieldtype: "Text Editor",
			label: is_internal_note ? __("Internal Note") : __("Reply"),
			reqd: 1,
		},
		({ comment }) => {
			frappe.call({
				method: "help_pilot.api.add_comment",
				args: { ticket: frm.doc.name, comment, is_internal_note },
				callback: () => {
					frappe.show_alert({ message: __("Reply added"), indicator: "green" });
					frm.reload_doc();
				},
			});
		},
		is_internal_note ? __("Add Internal Note") : __("Add Reply"),
		__("Post")
	);
}
