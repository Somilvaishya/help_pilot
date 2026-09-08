# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""The hub side of cross-site ticketing.

A bridge credential lives in another site's `site_config.json`, which is a
softer target than this hub. These tests pin down exactly how far one can
reach: its own source site, and the one email address it names. Nothing else.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from help_pilot import bridge

BRIDGE_A = "bridge-a@test.local"
BRIDGE_B = "bridge-b@test.local"
AGENT = "hp.bridgeagent@test.local"
REQUESTER = "priya.sharma@test.local"
OTHER = "vikram.rao@test.local"

SITE_A = "site-a.test"
SITE_B = "site-b.test"


def make_user(email, name, roles):
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": name,
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		).insert(ignore_permissions=True)
	else:
		user = frappe.get_doc("User", email)

	existing = {row.role for row in user.roles}
	for role in roles:
		if role not in existing:
			user.append("roles", {"role": role})
	user.save(ignore_permissions=True)
	return email


def make_source_site(name, bridge_user, department="Bridge IT"):
	if frappe.db.exists("HP Source Site", name):
		frappe.delete_doc("HP Source Site", name, force=1, ignore_permissions=True)

	return frappe.get_doc(
		{
			"doctype": "HP Source Site",
			"site_name": name,
			"base_url": f"https://{name}",
			"bridge_user": bridge_user,
			"is_active": 1,
			"default_department": department,
		}
	).insert(ignore_permissions=True)


class BaseBridgeTest(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.bridge_a = make_user(BRIDGE_A, "Bridge A", ["HP Bridge"])
		cls.bridge_b = make_user(BRIDGE_B, "Bridge B", ["HP Bridge"])
		cls.agent = make_user(AGENT, "Bridge Agent", ["HP User", "HP Agent"])

		if not frappe.db.exists("HP Department", "Bridge IT"):
			dept = frappe.get_doc(
				{"doctype": "HP Department", "department_name": "Bridge IT", "is_active": 1}
			)
			dept.append("members", {"user": cls.agent, "member_role": "Agent"})
			dept.insert(ignore_permissions=True)

	def setUp(self):
		frappe.set_user("Administrator")
		for name in frappe.get_all(
			"HP Ticket", filters={"source_site": ["in", [SITE_A, SITE_B]]}, pluck="name"
		):
			frappe.delete_doc("HP Ticket", name, force=1, ignore_permissions=True)

		make_source_site(SITE_A, self.bridge_a)
		make_source_site(SITE_B, self.bridge_b)

	def tearDown(self):
		frappe.set_user("Administrator")

	def raise_via_bridge(self, as_bridge, site, email, subject="Screen freezes", reference=None):
		frappe.set_user(as_bridge)
		try:
			return bridge.create_ticket(
				source_site=site,
				requester_email=email,
				subject=subject,
				description="<p>Detail.</p>",
				source_reference=reference,
			)
		finally:
			frappe.set_user("Administrator")


class TestBridgeAuthorisation(BaseBridgeTest):
	def test_bridge_cannot_act_for_another_site(self):
		frappe.set_user(self.bridge_b)
		with self.assertRaises(frappe.PermissionError):
			bridge.create_ticket(
				source_site=SITE_A, requester_email=REQUESTER, subject="x", description="<p>x</p>"
			)
		frappe.set_user("Administrator")

	def test_non_bridge_account_is_refused(self):
		frappe.set_user(self.agent)
		with self.assertRaises(frappe.PermissionError):
			bridge.get_departments(source_site=SITE_A)
		frappe.set_user("Administrator")

	def test_inactive_site_is_refused(self):
		frappe.db.set_value("HP Source Site", SITE_A, "is_active", 0)
		frappe.set_user(self.bridge_a)
		with self.assertRaises(frappe.PermissionError):
			bridge.get_departments(source_site=SITE_A)
		frappe.set_user("Administrator")

	def test_over_privileged_bridge_account_is_rejected(self):
		admin_bridge = make_user("bridge-over@test.local", "Over", ["HP Bridge", "HP System Admin"])
		doc = frappe.get_doc(
			{
				"doctype": "HP Source Site",
				"site_name": "over.test",
				"bridge_user": admin_bridge,
				"is_active": 1,
				"default_department": "Bridge IT",
			}
		)
		self.assertRaises(frappe.ValidationError, doc.insert, ignore_permissions=True)

	def test_account_without_the_bridge_role_is_rejected(self):
		plain = make_user("bridge-none@test.local", "Plain", [])
		doc = frappe.get_doc(
			{
				"doctype": "HP Source Site",
				"site_name": "none.test",
				"bridge_user": plain,
				"is_active": 1,
				"default_department": "Bridge IT",
			}
		)
		self.assertRaises(frappe.ValidationError, doc.insert, ignore_permissions=True)

	def test_bridge_role_grants_no_read_on_tickets(self):
		frappe.set_user(self.bridge_a)
		has_read = frappe.has_permission("HP Ticket", "read")
		frappe.set_user("Administrator")
		self.assertFalse(has_read)


class TestBridgeCreate(BaseBridgeTest):
	def test_ticket_is_attributed_to_the_requester(self):
		result = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)
		row = frappe.db.get_value(
			"HP Ticket", result["name"], ["raised_by", "source_site", "status"], as_dict=True
		)
		self.assertEqual(row.raised_by, REQUESTER)
		self.assertEqual(row.source_site, SITE_A)
		self.assertEqual(row.status, "Open")

	def test_requester_is_provisioned_without_roles(self):
		self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)
		self.assertEqual(frappe.db.get_value("User", REQUESTER, "user_type"), "Website User")
		self.assertEqual(frappe.get_all("Has Role", filters={"parent": REQUESTER}, pluck="role"), [])

	def test_replayed_delivery_returns_the_original(self):
		first = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER, reference="outbox-1")
		second = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER, reference="outbox-1")

		self.assertEqual(first["name"], second["name"])
		self.assertTrue(second["duplicate"])
		self.assertEqual(frappe.db.count("HP Ticket", {"source_reference": "outbox-1"}), 1)

	def test_department_falls_back_to_the_site_default(self):
		result = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)
		self.assertEqual(frappe.db.get_value("HP Ticket", result["name"], "department"), "Bridge IT")

	def test_a_blank_subject_is_rejected(self):
		frappe.set_user(self.bridge_a)
		with self.assertRaises(frappe.ValidationError):
			bridge.create_ticket(
				source_site=SITE_A, requester_email=REQUESTER, subject="   ", description="<p>x</p>"
			)
		frappe.set_user("Administrator")

	def test_a_bad_email_is_rejected(self):
		frappe.set_user(self.bridge_a)
		with self.assertRaises(frappe.ValidationError):
			bridge.create_ticket(
				source_site=SITE_A, requester_email="not an email", subject="x", description="<p>x</p>"
			)
		frappe.set_user("Administrator")


class TestBridgeRead(BaseBridgeTest):
	def test_reads_are_scoped_to_site_and_requester(self):
		mine = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)
		theirs = self.raise_via_bridge(self.bridge_a, SITE_A, OTHER)

		frappe.set_user(self.bridge_a)
		listed = bridge.get_my_tickets(source_site=SITE_A, requester_email=REQUESTER)
		with self.assertRaises(frappe.PermissionError):
			bridge.get_ticket(source_site=SITE_A, requester_email=REQUESTER, ticket=theirs["name"])
		frappe.set_user("Administrator")

		self.assertEqual([t["name"] for t in listed], [mine["name"]])

	def test_another_site_cannot_read_this_sites_ticket(self):
		mine = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)

		frappe.set_user(self.bridge_b)
		with self.assertRaises(frappe.PermissionError):
			bridge.get_ticket(source_site=SITE_B, requester_email=REQUESTER, ticket=mine["name"])
		frappe.set_user("Administrator")

	def test_internal_notes_never_cross_the_bridge(self):
		ticket = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)["name"]

		frappe.set_user(self.agent)
		frappe.get_doc(
			{"doctype": "HP Ticket Comment", "ticket": ticket, "comment": "<p>Public.</p>"}
		).insert()
		frappe.get_doc(
			{
				"doctype": "HP Ticket Comment",
				"ticket": ticket,
				"comment": "<p>Internal.</p>",
				"is_internal_note": 1,
			}
		).insert()
		frappe.set_user("Administrator")

		frappe.set_user(self.bridge_a)
		detail = bridge.get_ticket(source_site=SITE_A, requester_email=REQUESTER, ticket=ticket)
		frappe.set_user("Administrator")

		self.assertEqual(len(detail["comments"]), 1)
		self.assertNotIn("Internal", detail["comments"][0]["comment"])


class TestBridgeWrite(BaseBridgeTest):
	def test_reply_is_attributed_to_the_requester(self):
		ticket = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)["name"]

		frappe.set_user(self.bridge_a)
		bridge.add_reply(
			source_site=SITE_A, requester_email=REQUESTER, ticket=ticket, comment="<p>Still broken.</p>"
		)
		detail = bridge.get_ticket(source_site=SITE_A, requester_email=REQUESTER, ticket=ticket)
		frappe.set_user("Administrator")

		self.assertEqual(detail["comments"][0]["comment_by"], REQUESTER)
		self.assertTrue(detail["comments"][0]["is_mine"])

	def test_requester_may_only_close_or_reopen(self):
		ticket = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)["name"]

		frappe.set_user(self.bridge_a)
		with self.assertRaises(frappe.ValidationError):
			bridge.set_status(
				source_site=SITE_A, requester_email=REQUESTER, ticket=ticket, status="In Progress"
			)
		frappe.set_user("Administrator")

	def test_requester_can_reopen_a_resolved_ticket(self):
		ticket = self.raise_via_bridge(self.bridge_a, SITE_A, REQUESTER)["name"]

		frappe.set_user(self.agent)
		doc = frappe.get_doc("HP Ticket", ticket)
		doc.status = "Resolved"
		doc.save()
		frappe.set_user("Administrator")

		frappe.set_user(self.bridge_a)
		result = bridge.set_status(
			source_site=SITE_A, requester_email=REQUESTER, ticket=ticket, status="Reopened"
		)
		frappe.set_user("Administrator")

		self.assertEqual(result["status"], "Reopened")
