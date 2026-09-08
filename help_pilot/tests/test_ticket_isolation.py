# Copyright (c) 2026, Somil Vaishya and contributors
# For license information, please see license.txt

"""The isolation guarantees Help Pilot exists for.

A requester must never see another person's ticket, and an agent must never see
a department they are not a member of. Everything here is a regression guard on
`help_pilot.permissions`.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

REQUESTER = "hp.requester@test.local"
IT_AGENT = "hp.itagent@test.local"
ERP_ADMIN = "hp.erpadmin@test.local"
SYS_ADMIN = "hp.sysadmin@test.local"


def make_user(email, first_name, roles):
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
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


def make_department(name, members):
	if frappe.db.exists("HP Department", name):
		frappe.delete_doc("HP Department", name, force=1, ignore_permissions=True)

	doc = frappe.get_doc({"doctype": "HP Department", "department_name": name, "is_active": 1})
	for user, member_role in members:
		doc.append("members", {"user": user, "member_role": member_role})
	return doc.insert(ignore_permissions=True)


class BaseHelpPilotTest(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.requester = make_user(REQUESTER, "Riya Requester", ["HP User"])
		cls.it_agent = make_user(IT_AGENT, "Ivan ITAgent", ["HP User", "HP Agent"])
		cls.erp_admin = make_user(ERP_ADMIN, "Esha ERPAdmin", ["HP User", "HP Department Admin"])
		cls.sys_admin = make_user(SYS_ADMIN, "Sam SysAdmin", ["HP User", "HP System Admin"])

		make_department("IT", [(cls.it_agent, "Agent")])
		make_department("ERP", [(cls.erp_admin, "Department Admin")])
		# No commit here: FrappeTestCase rolls each test back to a savepoint, and
		# committing would destroy it, leaking tickets between tests.

	def setUp(self):
		frappe.set_user("Administrator")
		# FrappeTestCase rolls back once per class, not per test, so each test
		# clears the fixtures the previous one left behind.
		self.clear_tickets()
		self.req_it = self.make_ticket(self.requester, "IT", "Laptop will not boot")
		self.req_erp = self.make_ticket(self.requester, "ERP", "Invoice print format broken")
		self.agent_erp = self.make_ticket(self.it_agent, "ERP", "Need report access in ERP")
		self.all_tickets = [self.req_it, self.req_erp, self.agent_erp]

	def tearDown(self):
		frappe.set_user("Administrator")

	def clear_tickets(self):
		for name in frappe.get_all(
			"HP Ticket", filters={"raised_by": ["like", "%@test.local"]}, pluck="name"
		):
			frappe.delete_doc("HP Ticket", name, force=1, ignore_permissions=True)

	def make_ticket(self, user, department, subject):
		frappe.set_user(user)
		doc = frappe.get_doc(
			{
				"doctype": "HP Ticket",
				"subject": subject,
				"department": department,
				"description": f"<p>{subject}</p>",
				"priority": "Medium",
			}
		).insert()
		frappe.set_user("Administrator")
		return doc.name

	def visible_to(self, user):
		frappe.set_user(user)
		names = set(
			frappe.get_list("HP Ticket", filters={"name": ["in", self.all_tickets]}, pluck="name")
		)
		frappe.set_user("Administrator")
		return names

	def can_read(self, user, ticket):
		frappe.set_user(user)
		try:
			frappe.get_doc("HP Ticket", ticket).check_permission("read")
			return True
		except frappe.PermissionError:
			return False
		finally:
			frappe.set_user("Administrator")


class TestTicketIsolation(BaseHelpPilotTest):
	def test_requester_sees_only_own_tickets(self):
		self.assertEqual(self.visible_to(self.requester), {self.req_it, self.req_erp})

	def test_agent_sees_department_queue_and_own_tickets(self):
		self.assertEqual(self.visible_to(self.it_agent), {self.req_it, self.agent_erp})

	def test_department_admin_sees_only_their_department(self):
		self.assertEqual(self.visible_to(self.erp_admin), {self.req_erp, self.agent_erp})

	def test_system_admin_sees_everything(self):
		self.assertEqual(self.visible_to(self.sys_admin), set(self.all_tickets))

	def test_direct_document_access_is_blocked_too(self):
		self.assertFalse(self.can_read(self.requester, self.agent_erp))
		self.assertFalse(self.can_read(self.it_agent, self.req_erp))
		self.assertTrue(self.can_read(self.erp_admin, self.req_erp))

	def test_raised_by_is_stamped_from_session(self):
		self.assertEqual(frappe.db.get_value("HP Ticket", self.req_it, "raised_by"), self.requester)
		self.assertEqual(frappe.db.get_value("HP Ticket", self.req_it, "status"), "Open")


class TestTicketLifecycle(BaseHelpPilotTest):
	def test_requester_cannot_start_progress(self):
		frappe.set_user(self.requester)
		doc = frappe.get_doc("HP Ticket", self.req_it)
		doc.status = "In Progress"
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_agent_resolve_stamps_resolution(self):
		frappe.set_user(self.it_agent)
		doc = frappe.get_doc("HP Ticket", self.req_it)
		doc.status = "Resolved"
		doc.resolution_details = "<p>Replaced the RAM stick.</p>"
		doc.save()

		self.assertTrue(doc.resolution_date)
		self.assertIsNotNone(doc.resolution_time)
		self.assertGreaterEqual(doc.resolution_time, 0)

	def test_requester_can_reopen_a_resolved_ticket(self):
		frappe.set_user(self.it_agent)
		doc = frappe.get_doc("HP Ticket", self.req_it)
		doc.status = "Resolved"
		doc.save()

		frappe.set_user(self.requester)
		doc = frappe.get_doc("HP Ticket", self.req_it)
		doc.status = "Reopened"
		doc.save()

		self.assertEqual(doc.status, "Reopened")
		self.assertEqual(doc.reopen_count, 1)
		self.assertIsNone(doc.resolution_date)

	def test_illegal_transition_is_rejected(self):
		doc = frappe.get_doc("HP Ticket", self.req_it)
		doc.status = "Closed"
		doc.save()

		doc.status = "Open"
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_agent_must_belong_to_the_department(self):
		doc = frappe.get_doc("HP Ticket", self.req_erp)
		doc.assigned_agent = self.it_agent
		self.assertRaises(frappe.ValidationError, doc.save)


class TestTicketComments(BaseHelpPilotTest):
	def add_comment(self, user, ticket, comment, is_internal_note=0):
		frappe.set_user(user)
		doc = frappe.get_doc(
			{
				"doctype": "HP Ticket Comment",
				"ticket": ticket,
				"comment": comment,
				"is_internal_note": is_internal_note,
			}
		).insert()
		frappe.set_user("Administrator")
		return doc

	def test_internal_notes_are_invisible_to_the_requester(self):
		from help_pilot.api import get_ticket_thread

		self.add_comment(self.it_agent, self.req_it, "<p>Looking into it.</p>")
		self.add_comment(self.it_agent, self.req_it, "<p>Warranty replacement.</p>", 1)

		frappe.set_user(self.requester)
		thread = get_ticket_thread(self.req_it)
		visible = frappe.get_list("HP Ticket Comment", filters={"ticket": self.req_it}, pluck="name")
		frappe.set_user("Administrator")

		self.assertEqual(len(thread), 1)
		self.assertFalse(any(row["is_internal_note"] for row in thread))
		self.assertEqual(len(visible), 1)

		frappe.set_user(self.it_agent)
		self.assertEqual(len(get_ticket_thread(self.req_it)), 2)
		frappe.set_user("Administrator")

	def test_first_agent_reply_stamps_first_response(self):
		self.add_comment(self.it_agent, self.req_it, "<p>On it.</p>")
		self.assertTrue(frappe.db.get_value("HP Ticket", self.req_it, "first_responded_on"))

	def test_requester_cannot_write_an_internal_note(self):
		frappe.set_user(self.requester)
		doc = frappe.get_doc(
			{
				"doctype": "HP Ticket Comment",
				"ticket": self.req_it,
				"comment": "<p>secret</p>",
				"is_internal_note": 1,
			}
		)
		self.assertRaises(frappe.PermissionError, doc.insert)
		frappe.set_user("Administrator")

	def test_outsider_cannot_comment(self):
		frappe.set_user(self.erp_admin)
		doc = frappe.get_doc(
			{"doctype": "HP Ticket Comment", "ticket": self.req_it, "comment": "<p>hello</p>"}
		)
		self.assertRaises((frappe.PermissionError, frappe.ValidationError), doc.insert)
		frappe.set_user("Administrator")


class TestDashboardApi(BaseHelpPilotTest):
	def test_my_stats_counts_only_own_tickets(self):
		from help_pilot.api import get_my_stats

		frappe.set_user(self.requester)
		stats = get_my_stats()
		frappe.set_user("Administrator")
		self.assertEqual(stats["total"], 2)

	def test_department_stats_are_scoped_to_membership(self):
		from help_pilot.api import get_department_stats

		frappe.set_user(self.it_agent)
		self.assertEqual(get_department_stats("IT")["total"], 1)
		self.assertRaises(frappe.PermissionError, get_department_stats, "ERP")
		frappe.set_user("Administrator")


class TestDepartment(BaseHelpPilotTest):
	def test_department_with_tickets_cannot_be_deleted(self):
		self.assertRaises(
			frappe.ValidationError, frappe.delete_doc, "HP Department", "IT", ignore_permissions=True
		)

	def test_duplicate_members_are_rejected(self):
		doc = frappe.get_doc("HP Department", "IT")
		doc.append("members", {"user": self.it_agent, "member_role": "Agent"})
		self.assertRaises(frappe.ValidationError, doc.save)
