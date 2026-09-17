# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""Issue categories, branch, contact number, live alerts and attachments."""

from unittest.mock import patch

import frappe

from help_pilot import bridge, realtime
from help_pilot.tests.test_bridge import OTHER, REQUESTER, SITE_A, BaseBridgeTest

IT = "Bridge IT"
HR = "Bridge HR"
HR_AGENT = "hp.hragent@test.local"


def make_hr_agent():
	from help_pilot.tests.test_bridge import make_user

	return make_user(HR_AGENT, "HR Agent", ["HP User", "HP Agent"])


def make_department(name):
	if not frappe.db.exists("HP Department", name):
		frappe.get_doc(
			{"doctype": "HP Department", "department_name": name, "is_active": 1}
		).insert(ignore_permissions=True)
	return name


def make_category(name, department=IT, priority=None, active=1):
	make_department(department)
	full = f"{department} - {name}"
	if frappe.db.exists("HP Issue Category", full):
		frappe.delete_doc("HP Issue Category", full, force=1, ignore_permissions=True)

	return frappe.get_doc(
		{
			"doctype": "HP Issue Category",
			"category_name": name,
			"department": department,
			"is_active": active,
			"default_priority": priority,
		}
	).insert(ignore_permissions=True)


class TestIssueCategory(BaseBridgeTest):
	def test_a_category_is_named_for_its_department(self):
		self.assertEqual(make_category("Laptop").name, f"{IT} - Laptop")

	def test_the_same_name_can_exist_in_two_departments(self):
		a = make_category("Other", IT)
		b = make_category("Other", HR)
		self.assertNotEqual(a.name, b.name)

	def test_a_ticket_rejects_a_category_from_another_department(self):
		hr_category = make_category("Payslip", HR)

		doc = frappe.get_doc(
			{
				"doctype": "HP Ticket",
				"subject": "Wrong category",
				"department": IT,
				"description": "<p>x</p>",
				"issue_category": hr_category.name,
				"raised_by": REQUESTER,
			}
		)
		self.assertRaises(frappe.ValidationError, doc.insert, ignore_permissions=True)

	def test_bridge_accepts_a_bare_category_name(self):
		make_category("Laptop")
		frappe.set_user(self.bridge_a)
		result = bridge.create_ticket(
			source_site=SITE_A,
			requester_email=REQUESTER,
			subject="Screen flicker",
			description="<p>x</p>",
			issue_category="Laptop",
		)
		frappe.set_user("Administrator")

		self.assertEqual(
			frappe.db.get_value("HP Ticket", result["name"], "issue_category"), f"{IT} - Laptop"
		)

	def test_an_unknown_category_is_rejected(self):
		frappe.set_user(self.bridge_a)
		with self.assertRaises(frappe.ValidationError):
			bridge.create_ticket(
				source_site=SITE_A,
				requester_email=REQUESTER,
				subject="x",
				description="<p>x</p>",
				issue_category="Nonsense",
			)
		frappe.set_user("Administrator")

	def test_a_category_can_raise_the_priority(self):
		make_category("Server Down", priority="Urgent")
		frappe.set_user(self.bridge_a)
		result = bridge.create_ticket(
			source_site=SITE_A,
			requester_email=REQUESTER,
			subject="Everything is down",
			description="<p>x</p>",
			issue_category="Server Down",
		)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("HP Ticket", result["name"], "priority"), "Urgent")

	def test_an_explicit_priority_beats_the_category_default(self):
		make_category("Server Down", priority="Urgent")
		frappe.set_user(self.bridge_a)
		result = bridge.create_ticket(
			source_site=SITE_A,
			requester_email=REQUESTER,
			subject="Minor",
			description="<p>x</p>",
			issue_category="Server Down",
			priority="Low",
		)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("HP Ticket", result["name"], "priority"), "Low")

	def test_get_categories_is_scoped_to_the_department(self):
		make_category("Laptop", IT)
		make_category("Payslip", HR)

		frappe.set_user(self.bridge_a)
		rows = bridge.get_categories(source_site=SITE_A, department=IT)
		frappe.set_user("Administrator")

		self.assertTrue(all(r["department"] == IT for r in rows))
		self.assertIn(f"{IT} - Laptop", [r["name"] for r in rows])


class TestBranchAndContact(BaseBridgeTest):
	def raise_with_branch(self, branch, contact=None):
		frappe.set_user(self.bridge_a)
		try:
			return bridge.create_ticket(
				source_site=SITE_A,
				requester_email=REQUESTER,
				subject="Branch test",
				description="<p>x</p>",
				branch=branch,
				contact_no=contact,
			)
		finally:
			frappe.set_user("Administrator")

	def test_the_branch_is_kept_as_typed(self):
		result = self.raise_with_branch("Kanpur Depot", "98765 43210")

		row = frappe.db.get_value("HP Ticket", result["name"], ["branch", "contact_no"], as_dict=True)
		self.assertEqual(row.branch, "Kanpur Depot")
		self.assertEqual(row.contact_no, "98765 43210")

	def test_nothing_is_created_in_the_erpnext_branch_master(self):
		before = frappe.db.count("Branch") if frappe.db.exists("DocType", "Branch") else 0
		self.raise_with_branch("Somewhere Nobody Has Heard Of")
		after = frappe.db.count("Branch") if frappe.db.exists("DocType", "Branch") else 0

		# Plain text now: the hub must not fill erpnext's master with whatever
		# arrives from a client site.
		self.assertEqual(after, before)

	def test_a_branch_the_hub_has_never_seen_is_accepted(self):
		result = self.raise_with_branch("Brand New Depot")
		self.assertEqual(
			frappe.db.get_value("HP Ticket", result["name"], "branch"), "Brand New Depot"
		)

	def test_surrounding_space_is_trimmed(self):
		result = self.raise_with_branch("   Kanpur Depot   ")
		self.assertEqual(frappe.db.get_value("HP Ticket", result["name"], "branch"), "Kanpur Depot")

	def test_a_branch_of_only_spaces_counts_as_none(self):
		result = self.raise_with_branch("    ")
		self.assertIsNone(frappe.db.get_value("HP Ticket", result["name"], "branch"))

	def test_a_blank_branch_is_left_alone(self):
		frappe.set_user(self.bridge_a)
		result = bridge.create_ticket(
			source_site=SITE_A,
			requester_email=REQUESTER,
			subject="No branch",
			description="<p>x</p>",
		)
		frappe.set_user("Administrator")
		self.assertIsNone(frappe.db.get_value("HP Ticket", result["name"], "branch"))


class TestLiveAlerts(BaseBridgeTest):
	def alerted_users(self, pushed):
		return {call.kwargs.get("user") for call in pushed.call_args_list}

	def test_nobody_is_alerted_about_their_own_ticket(self):
		with patch("frappe.publish_realtime") as pushed:
			frappe.set_user(self.agent)
			frappe.get_doc(
				{
					"doctype": "HP Ticket",
					"subject": "Raised by the agent",
					"department": IT,
					"description": "<p>x</p>",
				}
			).insert()
			frappe.set_user("Administrator")

		self.assertNotIn(self.agent, self.alerted_users(pushed))

	def test_a_reply_alerts_the_other_side(self):
		ticket = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)["name"]
		frappe.db.set_value("HP Ticket", ticket, "assigned_agent", self.agent)

		with patch("frappe.publish_realtime") as pushed:
			frappe.set_user(self.agent)
			frappe.get_doc(
				{"doctype": "HP Ticket Comment", "ticket": ticket, "comment": "<p>On it.</p>"}
			).insert()
			frappe.set_user("Administrator")

		alerted = self.alerted_users(pushed)
		self.assertIn(REQUESTER, alerted)
		self.assertNotIn(self.agent, alerted)

	def test_an_internal_note_never_alerts_the_requester(self):
		ticket = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)["name"]

		with patch("frappe.publish_realtime") as pushed:
			frappe.set_user(self.agent)
			frappe.get_doc(
				{
					"doctype": "HP Ticket Comment",
					"ticket": ticket,
					"comment": "<p>Internal.</p>",
					"is_internal_note": 1,
				}
			).insert()
			frappe.set_user("Administrator")

		self.assertNotIn(REQUESTER, self.alerted_users(pushed))

	def test_an_alert_carries_a_sound_and_a_route(self):
		ticket = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)["name"]
		frappe.db.set_value("HP Ticket", ticket, "assigned_agent", self.agent)

		with patch("frappe.publish_realtime") as pushed:
			frappe.set_user(self.agent)
			frappe.get_doc(
				{"doctype": "HP Ticket Comment", "ticket": ticket, "comment": "<p>Hello.</p>"}
			).insert()
			frappe.set_user("Administrator")

		payloads = [call.args[1] for call in pushed.call_args_list if len(call.args) > 1]
		self.assertTrue(payloads)
		self.assertEqual(payloads[0]["sound"], realtime.SOUND_REPLY)
		self.assertIn(ticket, payloads[0]["route"])


class TestRequesterProvisioning(BaseBridgeTest):
	"""A brand new email must be able to raise its very first ticket.

	Other apps hook User creation and insert their own documents without
	ignore_permissions. Under the bridge session -- which holds almost nothing --
	those blew up and killed the ticket. Provisioning now runs as the system.
	"""

	def setUp(self):
		super().setUp()
		# A fresh address every time. Deleting a User does not always take the
		# rows other apps hang off it, and a half-removed identity makes the
		# next insert fail for reasons that have nothing to do with Help Pilot.
		self.newcomer = f"first.timer.{frappe.generate_hash(length=8)}@test.local"

	def test_an_unknown_email_can_raise_its_first_ticket(self):
		frappe.set_user(self.bridge_a)
		result = bridge.create_ticket(
			source_site=SITE_A,
			requester_email=self.newcomer,
			requester_full_name="First Timer",
			subject="My very first ticket",
			description="<p>x</p>",
		)
		frappe.set_user("Administrator")

		self.assertEqual(
			frappe.db.get_value("HP Ticket", result["name"], "raised_by"), self.newcomer
		)
		self.assertEqual(frappe.db.get_value("User", self.newcomer, "user_type"), "Website User")

	def test_the_session_is_handed_back_afterwards(self):
		frappe.set_user(self.bridge_a)
		try:
			bridge.create_ticket(
				source_site=SITE_A,
				requester_email=self.newcomer,
				subject="Session check",
				description="<p>x</p>",
			)
			# Provisioning briefly becomes Administrator; it must not stay that way,
			# or every later call in this request runs with full rights.
			self.assertEqual(frappe.session.user, self.bridge_a)
		finally:
			frappe.set_user("Administrator")


class TestTransfer(BaseBridgeTest):
	"""Moving a ticket keeps everything; closing it with "not our work" does not."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_department(HR)
		make_hr_agent()
		hr = frappe.get_doc("HP Department", HR)
		if not any(row.user == cls.agent for row in hr.members):
			# A second agent who belongs to HR but not IT, to prove the handover.
			hr.append("members", {"user": HR_AGENT, "member_role": "Agent"})
			hr.save(ignore_permissions=True)

	def a_ticket(self):
		return self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)["name"]

	def transfer(self, ticket, to=HR, reason="Belongs to payroll.", category=None, as_user=None):
		from help_pilot import api

		frappe.set_user(as_user or self.agent)
		try:
			return api.transfer_ticket(
				ticket=ticket, department=to, issue_category=category, reason=reason
			)
		finally:
			frappe.set_user("Administrator")

	def test_the_ticket_lands_in_the_new_department(self):
		ticket = self.a_ticket()
		result = self.transfer(ticket)

		self.assertEqual(result["department"], HR)
		self.assertEqual(frappe.db.get_value("HP Ticket", ticket, "department"), HR)

	def test_the_old_assignment_and_category_do_not_follow(self):
		ticket = self.a_ticket()
		make_category("Laptop", IT)
		frappe.db.set_value(
			"HP Ticket", ticket, {"assigned_agent": self.agent, "issue_category": f"{IT} - Laptop"}
		)

		self.transfer(ticket)

		row = frappe.db.get_value(
			"HP Ticket", ticket, ["assigned_agent", "issue_category"], as_dict=True
		)
		self.assertIsNone(row.assigned_agent)
		self.assertIsNone(row.issue_category)

	def test_a_category_in_the_new_department_can_be_set_on_the_way(self):
		ticket = self.a_ticket()
		payslip = make_category("Payslip", HR)

		self.transfer(ticket, category=payslip.name)

		self.assertEqual(
			frappe.db.get_value("HP Ticket", ticket, "issue_category"), payslip.name
		)

	def test_the_thread_and_age_survive(self):
		ticket = self.a_ticket()
		opened = frappe.db.get_value("HP Ticket", ticket, "opening_datetime")

		frappe.set_user(self.agent)
		frappe.get_doc(
			{"doctype": "HP Ticket Comment", "ticket": ticket, "comment": "<p>Earlier reply.</p>"}
		).insert()
		frappe.set_user("Administrator")

		self.transfer(ticket)

		self.assertEqual(frappe.db.get_value("HP Ticket", ticket, "opening_datetime"), opened)
		comments = frappe.get_all(
			"HP Ticket Comment", filters={"ticket": ticket}, fields=["comment", "is_internal_note"]
		)
		self.assertTrue(any("Earlier reply" in c.comment for c in comments))

	def test_the_reason_is_internal_and_the_move_is_not(self):
		ticket = self.a_ticket()
		self.transfer(ticket, reason="Payroll data, not ours.")

		comments = frappe.get_all(
			"HP Ticket Comment",
			filters={"ticket": ticket},
			fields=["comment", "is_internal_note"],
			order_by="creation asc",
		)

		internal = [c for c in comments if c.is_internal_note]
		public = [c for c in comments if not c.is_internal_note]

		self.assertTrue(any("Payroll data" in c.comment for c in internal))
		# The requester must never see the reason, only that it moved.
		self.assertFalse(any("Payroll data" in c.comment for c in public))
		self.assertTrue(any(HR in c.comment for c in public))

	def test_a_reason_is_required(self):
		ticket = self.a_ticket()
		with self.assertRaises(frappe.ValidationError):
			self.transfer(ticket, reason="   ")

	def test_you_cannot_move_a_ticket_that_is_not_yours(self):
		ticket = self.a_ticket()
		with self.assertRaises(frappe.PermissionError):
			self.transfer(ticket, as_user=HR_AGENT)

	def test_moving_it_nowhere_is_rejected(self):
		ticket = self.a_ticket()
		with self.assertRaises(frappe.ValidationError):
			self.transfer(ticket, to=IT)

	def test_the_mover_is_told_when_it_leaves_their_sight(self):
		ticket = self.a_ticket()
		result = self.transfer(ticket)

		# The agent belongs to IT, not HR, so isolation takes it away from them.
		self.assertFalse(result["still_visible"])

	def test_the_receiving_team_is_alerted(self):
		ticket = self.a_ticket()

		with patch("frappe.publish_realtime") as pushed:
			self.transfer(ticket)

		alerted = {call.kwargs.get("user") for call in pushed.call_args_list}
		self.assertIn(HR_AGENT, alerted)


class TestTicketAttachments(BaseBridgeTest):
	def test_attachments_are_scoped_like_everything_else(self):
		mine = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)["name"]
		theirs = self.raise_via_bridge(self.bridge_a, SITE_A, OTHER)["name"]

		frappe.set_user(self.bridge_a)
		files = bridge.get_attachments(source_site=SITE_A, requester_email=REQUESTER, ticket=mine)
		with self.assertRaises(frappe.PermissionError):
			bridge.get_attachments(source_site=SITE_A, requester_email=REQUESTER, ticket=theirs)
		frappe.set_user("Administrator")

		self.assertEqual(files, [])

	def test_a_second_file_can_follow_the_ticket(self):
		import base64

		ticket = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)["name"]

		frappe.set_user(self.bridge_a)
		bridge.attach_file(
			source_site=SITE_A,
			requester_email=REQUESTER,
			ticket=ticket,
			file_name="second.txt",
			content_base64=base64.b64encode(b"one more file").decode(),
		)
		files = bridge.get_attachments(source_site=SITE_A, requester_email=REQUESTER, ticket=ticket)
		frappe.set_user("Administrator")

		self.assertEqual([f["file_name"] for f in files], ["second.txt"])
