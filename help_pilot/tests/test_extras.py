# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""Issue categories, branch, contact number, live alerts and attachments."""

from unittest.mock import patch

import frappe

from help_pilot import bridge
from help_pilot.tests.test_bridge import OTHER, REQUESTER, SITE_A, BaseBridgeTest

IT = "Bridge IT"
HR = "Bridge HR"


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
	def test_an_unknown_branch_is_created_on_the_hub(self):
		if frappe.db.exists("Branch", "Kanpur Depot"):
			frappe.delete_doc("Branch", "Kanpur Depot", force=1, ignore_permissions=True)

		frappe.set_user(self.bridge_a)
		result = bridge.create_ticket(
			source_site=SITE_A,
			requester_email=REQUESTER,
			subject="Branch test",
			description="<p>x</p>",
			branch="Kanpur Depot",
			contact_no="98765 43210",
		)
		frappe.set_user("Administrator")

		row = frappe.db.get_value("HP Ticket", result["name"], ["branch", "contact_no"], as_dict=True)
		self.assertEqual(row.branch, "Kanpur Depot")
		self.assertEqual(row.contact_no, "98765 43210")
		self.assertTrue(frappe.db.exists("Branch", "Kanpur Depot"))

	def test_an_existing_branch_is_reused_not_duplicated(self):
		if not frappe.db.exists("Branch", "Head Office"):
			frappe.get_doc({"doctype": "Branch", "branch": "Head Office"}).insert(
				ignore_permissions=True
			)

		frappe.set_user(self.bridge_a)
		bridge.create_ticket(
			source_site=SITE_A,
			requester_email=REQUESTER,
			subject="Branch reuse",
			description="<p>x</p>",
			branch="Head Office",
		)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Branch", {"branch": "Head Office"}), 1)

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
		self.assertEqual(payloads[0]["sound"], "email")
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
