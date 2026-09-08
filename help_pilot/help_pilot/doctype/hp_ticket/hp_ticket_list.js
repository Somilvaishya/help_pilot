frappe.listview_settings["HP Ticket"] = {
	add_fields: ["status", "priority"],
	get_indicator(doc) {
		const colors = {
			Open: "orange",
			"In Progress": "blue",
			Resolved: "green",
			Closed: "gray",
			Reopened: "red",
		};
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
	formatters: {
		priority(value) {
			const colors = { Low: "gray", Medium: "blue", High: "orange", Urgent: "red" };
			return `<span class="indicator-pill ${colors[value] || "gray"}">${__(value)}</span>`;
		},
	},
};
