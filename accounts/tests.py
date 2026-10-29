from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from inventory.models import Operation, Product


class DemoUsersTests(TestCase):
    def test_migration_created_three_users_with_roles(self):
        users = dict(get_user_model().objects.values_list("username", "role"))
        self.assertEqual(users, {"director": "director", "storekeeper": "storekeeper", "manager": "manager"})
        for username, password in [("director", "Director2026!"), ("storekeeper", "Store2026!"), ("manager", "Manager2026!")]:
            with self.subTest(username=username):
                self.assertTrue(get_user_model().objects.get(username=username).check_password(password))


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()

    def post_login(self, username, password, **extra):
        return self.client.post(reverse("login"), {"username": username, "password": password, **extra})

    def test_success_redirects_home(self):
        response = self.post_login("director", "Director2026!")
        self.assertRedirects(response, reverse("inventory:stock"))

    def test_wrong_password_and_unknown_user_get_the_same_generic_error(self):
        for username, password in [("director", "nope"), ("ghost", "nope")]:
            with self.subTest(username=username):
                response = self.post_login(username, password)
                self.assertContains(response, "Неверный логин или пароль")

    def test_five_failures_block_for_five_minutes_even_with_the_right_password(self):
        for _ in range(5):
            self.post_login("director", "wrong")
        response = self.post_login("director", "Director2026!")
        self.assertContains(response, "Слишком много неудачных попыток")
        self.assertNotIn("_auth_user_id", self.client.session)
        # Another login from the same IP is not affected.
        self.assertEqual(self.post_login("manager", "Manager2026!").status_code, 302)

    def test_success_resets_the_counter(self):
        for _ in range(4):
            self.post_login("director", "wrong")
        self.assertEqual(self.post_login("director", "Director2026!").status_code, 302)
        self.client.post(reverse("logout"))
        for _ in range(4):
            self.post_login("director", "wrong")
        self.assertEqual(self.post_login("director", "Director2026!").status_code, 302)

    def test_username_case_and_stray_spaces_are_forgiven(self):
        for username, password in [
            ("Director", "Director2026!"),
            ("DIRECTOR", "Director2026!"),
            ("  director  ", "Director2026!"),
            ("director", "Director2026! "),
            ("director", " Director2026!\t"),
            ("StoreKeeper", "Store2026!"),
        ]:
            with self.subTest(username=username, password=password):
                client = Client()
                response = client.post(reverse("login"), {"username": username, "password": password})
                self.assertRedirects(response, reverse("inventory:stock"))
                self.assertEqual(client.session["_auth_user_id"], str(get_user_model().objects.get(username=username.strip().lower()).pk))

    def test_password_stays_case_sensitive(self):
        for password in ["director2026!", "DIRECTOR2026!", "Director2026"]:
            with self.subTest(password=password):
                self.assertContains(self.post_login("director", password), "Неверный логин или пароль")

    def test_case_variants_share_one_attempt_counter(self):
        for username in ["Director", "DIRECTOR", "director", "dIrEcToR", " Director "]:
            self.post_login(username, "wrong")
        self.assertContains(self.post_login("director", "Director2026!"), "Слишком много неудачных попыток")

    def test_exact_name_wins_when_two_users_differ_only_by_case(self):
        User = get_user_model()
        User.objects.create_user("ali", password="lower-pass-1", role="manager")
        User.objects.create_user("Ali", password="upper-pass-1", role="manager")
        for username, password in [("ali", "lower-pass-1"), ("Ali", "upper-pass-1")]:
            with self.subTest(username=username):
                client = Client()
                self.assertEqual(client.post(reverse("login"), {"username": username, "password": password}).status_code, 302)
                self.assertEqual(client.session["_auth_user_id"], str(User.objects.get(username=username).pk))
        self.assertContains(self.post_login("ALI", "lower-pass-1"), "Неверный логин или пароль")

    def test_next_only_redirects_inside_the_site(self):
        for target, expected in [
            ("/history/", "/history/"),
            ("https://evil.example/", "/"),
            ("//evil.example/", "/"),
            ("javascript:alert(1)", "/"),
            ("/\\evil.example", "/"),
        ]:
            with self.subTest(next=target):
                client = Client()
                response = client.post(
                    reverse("login"), {"username": "director", "password": "Director2026!", "next": target}
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], expected)

    def test_logout_is_post_only(self):
        self.client.force_login(get_user_model().objects.get(username="director"))
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertRedirects(self.client.post(reverse("logout")), reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_session_cookie_flags(self):
        response = self.post_login("director", "Director2026!")
        cookie = response.cookies["sessionid"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")


class SecurityHeaderTests(TestCase):
    def test_headers(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "same-origin")

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(get_user_model().objects.get(username="director"))
        product = Product.objects.create(name="Кабель", unit="m", price=1, currency="USD")
        response = client.post(
            reverse("inventory:receive"),
            {"product": product.pk, "quantity": "1", "idempotency_key": "6c1f8c1e-1111-4111-8111-111111111111"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Сессия устарела", status_code=403)
        self.assertEqual(Operation.objects.count(), 0)
