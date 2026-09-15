// Copyright (c) 2026, Somil Vaishya and contributors
// For license information, please see license.txt

frappe.ui.form.on("HP Ticket", {
	setup(frm) {
		frm.set_query("assigned_agent", () => ({
			query: "help_pilot.api.department_agent_query",
			filters: { department: frm.doc.department },
		}));

		frm.set_query("issue_category", () => ({
			filters: { department: frm.doc.department, is_active: 1 },
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
		// Both of these belong to the old department and cannot survive the move.
		if (frm.doc.assigned_agent) {
			frm.set_value("assigned_agent", null);
		}
		if (frm.doc.issue_category) {
			frm.set_value("issue_category", null);
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

	if (is_agent) {
		frm.add_custom_button(__("Move to another team"), () => transfer(frm));
	}
}

// Moving a ticket keeps the thread, the attachments and the age. Closing it
// with "not our work" makes the requester start over.
function transfer(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Move this ticket"),
		fields: [
			{
				fieldname: "department",
				fieldtype: "Link",
				options: "HP Department",
				label: __("Which team should have it?"),
				reqd: 1,
				get_query: () => ({ filters: { is_active: 1, name: ["!=", frm.doc.department] } }),
				onchange: () => {
					dialog.set_value("issue_category", "");
					dialog.set_df_property("issue_category", "hidden", !dialog.get_value("department"));
				},
			},
			{
				fieldname: "issue_category",
				fieldtype: "Link",
				options: "HP Issue Category",
				label: __("Category in that team"),
				hidden: 1,
				get_query: () => ({
					filters: { department: dialog.get_value("department"), is_active: 1 },
				}),
			},
			{
				fieldname: "reason",
				fieldtype: "Small Text",
				label: __("Why is it moving?"),
				description: __("Kept as an internal note. The requester only sees that it moved."),
				reqd: 1,
			},
		],
		primary_action_label: __("Move"),
		primary_action(values) {
			dialog.disable_primary_action();

			frappe.call({
				method: "help_pilot.api.transfer_ticket",
				args: { ticket: frm.doc.name, ...values },
				freeze: true,
				freeze_message: __("Moving..."),
				callback: ({ message }) => {
					dialog.hide();

					if (message && message.still_visible) {
						frappe.show_alert(
							{ message: __("Moved to {0}", [message.department]), indicator: "green" },
							7
						);
						frm.reload_doc();
						return;
					}

					// They are not in the receiving team, so the ticket is about to
					// disappear from their list. Say it plainly instead of letting
					// the screen go blank on them.
					frappe.msgprint({
						title: __("Moved"),
						indicator: "green",
						message: __(
							"This ticket is now with {0}. You are not a member of that team, so it will no longer appear in your list.",
							[message.department]
						),
					});
					frappe.set_route("List", "HP Ticket");
				},
				error: () => dialog.enable_primary_action(),
			});
		},
	});

	dialog.show();
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
