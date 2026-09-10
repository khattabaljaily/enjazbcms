"""
Seed a full demo Center with realistic Arabic data spanning the current week,
touching every model in the system (services, staff, clients, appointments,
billing, finance, products, store, notifications, support).

Usage:
    python manage.py seed_demo_data
    python manage.py seed_demo_data --slug my-demo
"""
import random
from datetime import timedelta, time as time_cls
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.core.models import ServiceType, Center, Settings, Notification, PlatformSettings, CenterBackup, SupportTicket, SupportMessage
from apps.accounts.models import Role, User, PERMISSIONS
from apps.services.models import ServiceCategory, Service, Package, PackageService
from apps.staff.models import Specialist
from apps.clients.models import Client
from apps.products.models import ProductCategory, Product, StockMovement, PurchaseInvoice, PurchaseInvoiceLine
from apps.appointments.models import Appointment, AppointmentService
from apps.billing.models import Invoice, InvoiceLine
from apps.finance.models import (
    Treasury, TreasuryMovement, Expense, ClientPayment,
    Advance, SalaryPayment, UserAdvance, UserSalaryPayment, Incentive,
)
from apps.store.models import OnlineBooking, StoreOrder, StoreOrderItem

DEMO_SLUG = 'demo-week'
DEMO_PASSWORD = 'Demo@1234'


class Command(BaseCommand):
    help = 'Seed a full demo center with realistic data spanning the current week, across every model.'

    def add_arguments(self, parser):
        parser.add_argument('--slug', default=DEMO_SLUG, help=f'Slug for the demo center (default: {DEMO_SLUG})')

    def handle(self, *args, **options):
        random.seed(42)
        slug = options['slug']
        today = timezone.localdate()
        # Sunday-start week (matches JS getDay()/work_days convention used across the app)
        week_start = today - timedelta(days=(today.weekday() + 1) % 7)
        week_end = week_start + timedelta(days=6)
        self.today = today
        self.week_start = week_start
        self.week_end = week_end

        with transaction.atomic():
            self._wipe_existing(slug)

            center = self._create_center(slug)
            self._create_settings(center)
            roles = self._create_roles(center)
            users = self._create_users(center, roles)
            categories = self._create_service_categories(center)
            services = self._create_services(center, categories)
            packages = self._create_packages(center, services)
            specialists = self._create_specialists(center, services)
            clients = self._create_clients(center)
            prod_categories = self._create_product_categories(center)
            products = self._create_products(center, prod_categories)
            treasury = self._create_treasury(center)
            self._create_purchase_invoice(center, products)
            # StockMovement.save() mutates product.stock via a freshly-fetched instance
            # (see _apply_purchase_effects' select_related fetch); refresh our in-memory
            # objects so later stock movements compute from the real current stock.
            for p in products:
                p.refresh_from_db()
            appointments = self._create_appointments(center, clients, specialists, services, packages)
            self._create_appointment_invoices(center, appointments)
            self._create_client_payment(center)
            self._create_pos_invoices(center, clients, specialists, services, products)
            self._create_expenses(center, treasury)
            self._create_advances_and_salaries(center, specialists, treasury)
            self._create_user_advances_and_salaries(center, users, treasury)
            self._create_incentives(center, specialists, users, treasury)
            self._create_online_bookings(center, services, clients)
            self._create_store_orders(center, products, clients)
            self._create_notifications(center)
            self._create_support_ticket(center, users)
            self._create_center_backup(center)
            PlatformSettings.get()

        self.stdout.write(self.style.SUCCESS(f'\n✓ Demo data seeded for center "{center.name}" (slug={center.slug})'))
        self.stdout.write(self.style.SUCCESS(f'  Week range: {week_start} → {week_end}'))
        self.stdout.write(self.style.SUCCESS('  Login accounts (password for all: %s):' % DEMO_PASSWORD))
        for u in users:
            self.stdout.write(self.style.SUCCESS(f'    - {u.username}  ({u.full_name})'))

    # ── cleanup ──────────────────────────────────────────────────────────
    def _wipe_existing(self, slug):
        """Remove any previous demo center with this slug so re-running is clean.

        Center itself has no cascading delete() override, and several FKs use
        PROTECT (Product.category, AppointmentService.service, Appointment.client,
        Invoice.client, ...), so a plain `center.delete()` raises ProtectedError.
        Delete in an order where PROTECT-ing rows are removed before their targets.
        """
        center = Center.objects.filter(slug=slug).first()
        if not center:
            return
        self.stdout.write(self.style.WARNING(f'Removing previous demo center "{center.name}"...'))

        StoreOrder.objects.filter(center=center).delete()
        OnlineBooking.objects.filter(center=center).delete()
        PurchaseInvoice.objects.filter(center=center).delete()
        Invoice.objects.filter(center=center).delete()
        Appointment.objects.filter(center=center).delete()
        Incentive.objects.filter(center=center).delete()
        UserAdvance.objects.filter(center=center).delete()
        UserSalaryPayment.objects.filter(center=center).delete()
        Advance.objects.filter(center=center).delete()
        SalaryPayment.objects.filter(center=center).delete()
        Expense.objects.filter(center=center).delete()
        ClientPayment.objects.filter(center=center).delete()
        Client.objects.filter(center=center).delete()
        Product.objects.filter(center=center).delete()
        ProductCategory.objects.filter(center=center).delete()
        Specialist.objects.filter(center=center).delete()
        Package.objects.filter(center=center).delete()
        Service.objects.filter(center=center).delete()
        ServiceCategory.objects.filter(center=center).delete()
        Treasury.objects.filter(center=center).delete()
        Notification.objects.filter(center=center).delete()
        SupportTicket.objects.filter(center=center).delete()
        CenterBackup.objects.filter(center=center).delete()
        Role.objects.filter(center=center).delete()
        User.objects.filter(center=center).delete()
        Settings.objects.filter(center=center).delete()
        center.delete()

    # ── core / tenant ────────────────────────────────────────────────────
    def _create_center(self, slug):
        service_type, _ = ServiceType.objects.get_or_create(
            name='صالون نسائي',
            defaults=dict(icon='fas fa-spa', color='#ec4899', description='صالونات التجميل النسائية', order=1),
        )
        center = Center.objects.create(
            name='مركز الإنجاز التجريبي',
            slug=slug,
            service_type=service_type,
            phone='+249911234567',
            email='demo@enjaz.test',
            address='شارع النيل، الخرطوم',
            city='الخرطوم',
            country='SD',
            plan='pro',
            plan_start=self.today - timedelta(days=60),
            plan_expires=self.today + timedelta(days=305),
            is_active=True,
            is_demo=True,
            timezone='Africa/Khartoum',
            currency='SDG',
            language='ar',
            max_staff=10,
            max_users=5,
        )
        self.stdout.write(f'✓ Center: {center.name}')
        return center

    def _create_settings(self, center):
        return Settings.objects.create(
            center=center,
            invoice_color='#ec4899',
            invoice_prefix='INV',
            invoice_next_number=1,
            invoice_footer='شكراً لزيارتكم مركز الإنجاز',
            show_tax_on_invoice=False,
            tax_percent=Decimal('0'),
            booking_enabled=True,
            booking_advance_days=30,
            slot_minutes=30,
            work_start=time_cls(9, 0),
            work_end=time_cls(20, 0),
            work_days='0,1,2,3,4,5',
            store_enabled=True,
            store_name='متجر الإنجاز',
            loyalty_enabled=True,
            points_per_currency=1,
            reminder_enabled=True,
            reminder_hours_before=24,
        )

    def _create_roles(self, center):
        all_perms = {f'{section}.{action}': True for section, actions in PERMISSIONS.items() for action in actions}
        reception_perms = {
            'appointments.view': True, 'appointments.add': True, 'appointments.edit': True,
            'clients.view': True, 'clients.add': True, 'clients.edit': True,
            'services.view': True, 'billing.view': True, 'billing.create': True, 'billing.pos': True,
            'products.view': True, 'store.view': True, 'reports.view': True,
        }
        specialist_perms = {
            'appointments.view': True, 'clients.view': True, 'services.view': True,
        }
        owner_role = Role.objects.create(center=center, name='مدير الحساب', permissions=all_perms, is_default=False)
        reception_role = Role.objects.create(center=center, name='استقبال', permissions=reception_perms, is_default=True)
        specialist_role = Role.objects.create(center=center, name='أخصائي', permissions=specialist_perms, is_default=False)
        self.stdout.write('✓ Roles: 3')
        return {'owner': owner_role, 'reception': reception_role, 'specialist': specialist_role}

    def _create_users(self, center, roles):
        owner = User.objects.create_user(
            username='demo_owner', password=DEMO_PASSWORD, center=center,
            full_name='محمد الأمين', phone='+249911000001', email='owner@enjaz.test',
            is_owner=True, is_staff=True, base_salary=Decimal('0'),
        )
        reception = User.objects.create_user(
            username='demo_reception', password=DEMO_PASSWORD, center=center,
            full_name='سارة أحمد', phone='+249911000002', email='reception@enjaz.test',
            role=roles['reception'], base_salary=Decimal('2200'),
        )
        staff_user = User.objects.create_user(
            username='demo_staff', password=DEMO_PASSWORD, center=center,
            full_name='هدى محمد', phone='+249911000003', email='staff@enjaz.test',
            role=roles['specialist'], base_salary=Decimal('1800'),
        )
        self.stdout.write('✓ Users: 3')
        return [owner, reception, staff_user]

    # ── services ─────────────────────────────────────────────────────────
    def _create_service_categories(self, center):
        cats = {
            'hair': ServiceCategory.objects.create(center=center, name='العناية بالشعر', icon='fas fa-cut', color='#ec4899', order=1),
            'skin': ServiceCategory.objects.create(center=center, name='العناية بالبشرة', icon='fas fa-spa', color='#06b6d4', order=2),
            'nails': ServiceCategory.objects.create(center=center, name='العناية بالأظافر', icon='fas fa-hand-sparkles', color='#a855f7', order=3),
        }
        self.stdout.write('✓ Service categories: 3')
        return cats

    def _create_services(self, center, cats):
        data = [
            ('hair', 'قص شعر', 60, '150', '50'),
            ('hair', 'صبغة شعر', 90, '400', '150'),
            ('hair', 'بروتين شعر', 120, '600', '250'),
            ('skin', 'تنظيف بشرة', 45, '200', '60'),
            ('skin', 'تقشير الوجه', 30, '150', '40'),
            ('nails', 'مانيكير', 30, '80', '20'),
            ('nails', 'باديكير', 45, '100', '30'),
            ('nails', 'مانيكير جل', 45, '150', '40'),
        ]
        services = []
        for i, (cat_key, name, duration, price, cost) in enumerate(data):
            services.append(Service.objects.create(
                center=center, category=cats[cat_key], name=name,
                duration=duration, price=Decimal(price), cost=Decimal(cost),
                order=i, show_in_store=True,
            ))
        self.stdout.write(f'✓ Services: {len(services)}')
        return services

    def _create_packages(self, center, services):
        by_name = {s.name: s for s in services}
        bride = Package.objects.create(center=center, name='باقة العروس', description='قص وصبغة وتنظيف بشرة ومانيكير جل', price=Decimal('900'))
        for i, name in enumerate(['قص شعر', 'صبغة شعر', 'تنظيف بشرة', 'مانيكير جل']):
            PackageService.objects.create(package=bride, service=by_name[name], order=i)

        full_care = Package.objects.create(center=center, name='باقة العناية الشاملة', description='تنظيف بشرة وتقشير ومانيكير', price=Decimal('350'))
        for i, name in enumerate(['تنظيف بشرة', 'تقشير الوجه', 'مانيكير']):
            PackageService.objects.create(package=full_care, service=by_name[name], order=i)

        self.stdout.write('✓ Packages: 2')
        return [bride, full_care]

    # ── staff ────────────────────────────────────────────────────────────
    def _create_specialists(self, center, services):
        by_name = {s.name: s for s in services}
        specs = []

        huda = Specialist.objects.create(
            center=center, name='هدى محمد', phone='+249911000003', specialty='تصفيف وصبغ الشعر',
            color='#6366f1', work_start=time_cls(9, 0), work_end=time_cls(17, 0),
            working_days=[0, 1, 2, 3, 4, 5], salary_type='commission', commission_rate=Decimal('20'), order=1,
        )
        huda.services.set([by_name['قص شعر'], by_name['صبغة شعر'], by_name['بروتين شعر']])
        specs.append(huda)

        mona = Specialist.objects.create(
            center=center, name='منى العطا', phone='+249911000004', specialty='العناية بالبشرة',
            color='#06b6d4', work_start=time_cls(10, 0), work_end=time_cls(18, 0),
            working_days=[0, 1, 2, 3, 4, 5], salary_type='fixed', base_salary=Decimal('3000'), order=2,
        )
        mona.services.set([by_name['تنظيف بشرة'], by_name['تقشير الوجه']])
        specs.append(mona)

        reem = Specialist.objects.create(
            center=center, name='ريم بابكر', phone='+249911000005', specialty='العناية بالأظافر',
            color='#a855f7', work_start=time_cls(9, 0), work_end=time_cls(16, 0),
            working_days=[0, 1, 2, 3, 4, 6], salary_type='both', base_salary=Decimal('1500'), commission_rate=Decimal('10'), order=3,
        )
        reem.services.set([by_name['مانيكير'], by_name['باديكير'], by_name['مانيكير جل']])
        specs.append(reem)

        self.stdout.write(f'✓ Specialists: {len(specs)}')
        return specs

    def _create_clients(self, center):
        data = [
            ('إيمان عبدالله', 'f', 'referral', 120),
            ('نور الهدى حسن', 'f', 'social', 40),
            ('فاطمة الزهراء', 'f', 'walk_in', 0),
            ('مريم صديق', 'f', 'website', 200),
            ('زينب الطيب', 'f', 'social', 15),
            ('سلمى كمال', 'f', 'referral', 60),
            ('آية محجوب', 'f', 'other', 0),
            ('خالد إبراهيم', 'm', 'walk_in', 10),
            ('عمر الفاتح', 'm', 'social', 0),
            ('ياسمين النور', 'f', 'website', 90),
        ]
        clients = []
        for i, (name, gender, referral, points) in enumerate(data):
            clients.append(Client.objects.create(
                center=center, name=name, phone=f'+24991200{i:04d}',
                gender=gender, referral=referral, points=points,
                birthdate=self.today.replace(year=self.today.year - random.randint(20, 45)),
            ))
        self.stdout.write(f'✓ Clients: {len(clients)}')
        return clients

    # ── products ─────────────────────────────────────────────────────────
    def _create_product_categories(self, center):
        cats = {
            'hair': ProductCategory.objects.create(center=center, name='العناية بالشعر', order=1),
            'skin': ProductCategory.objects.create(center=center, name='العناية بالبشرة', order=2),
            'acc': ProductCategory.objects.create(center=center, name='إكسسوارات', order=3),
        }
        self.stdout.write('✓ Product categories: 3')
        return cats

    def _create_products(self, center, cats):
        data = [
            ('hair', 'شامبو مغذي', 'SKU-001', '80', '35'),
            ('hair', 'بلسم للشعر الجاف', 'SKU-002', '90', '40'),
            ('hair', 'زيت أرغان', 'SKU-003', '120', '55'),
            ('skin', 'كريم مرطب للوجه', 'SKU-004', '150', '70'),
            ('skin', 'واقي شمس SPF50', 'SKU-005', '180', '80'),
            ('skin', 'ماسك تنظيف عميق', 'SKU-006', '100', '45'),
            ('acc', 'طلاء أظافر', 'SKU-007', '60', '25'),
            ('acc', 'فرشاة شعر احترافية', 'SKU-008', '70', '30'),
        ]
        products = []
        for cat_key, name, sku, price, cost in data:
            products.append(Product.objects.create(
                center=center, category=cats[cat_key], name=name, sku=sku,
                price=Decimal(price), cost=Decimal(cost), stock=Decimal('0'), min_stock=Decimal('5'),
            ))
        self.stdout.write(f'✓ Products: {len(products)}')
        return products

    def _create_treasury(self, center):
        # A default Treasury is auto-created by apps.finance.signals.create_center_treasury
        # when the Center itself is saved — reuse it instead of creating a duplicate.
        treasury = Treasury.objects.get(center=center)
        treasury.set_initial_balance(Decimal('50000'), notes='رصيد افتتاحي تجريبي')
        self.stdout.write('✓ Treasury opening balance set to 50,000')
        return treasury

    def _create_purchase_invoice(self, center, products):
        invoice = PurchaseInvoice.objects.create(
            center=center, number='PUR-0001', supplier='شركة الجمال للتوريدات',
            date=self.week_start, payment_method='cash', paid=True,
        )
        for product in products:
            qty = Decimal(random.choice([20, 25, 30, 40]))
            line = PurchaseInvoiceLine.objects.create(
                invoice=invoice, product=product, quantity=qty, unit_cost=product.cost,
            )
        invoice.recalculate()
        self._apply_purchase_effects(invoice)
        self.stdout.write(f'✓ Purchase invoice with {len(products)} lines (stock received)')
        return invoice

    def _apply_purchase_effects(self, invoice):
        treasury = Treasury.objects.filter(center=invoice.center).first()
        ref = f'purchase_{invoice.pk}'
        for line in invoice.lines.select_related('product'):
            StockMovement.objects.create(
                center=invoice.center, product=line.product, change=line.quantity,
                type='purchase', reference=ref, notes=invoice.supplier,
            )
        if invoice.payment_method == 'cash' and treasury:
            TreasuryMovement.objects.create(
                treasury=treasury, type='out', amount=invoice.total,
                reference=ref, notes=f'مشتريات {invoice.number}',
            )

    # ── appointments + invoices ──────────────────────────────────────────
    def _create_appointments(self, center, clients, specialists, services, packages):
        spec_services = {s.pk: list(s.services.all()) for s in specialists}
        appointments = []
        day_offsets = list(range(7))

        for day_offset in day_offsets:
            d = self.week_start + timedelta(days=day_offset)
            if d > self.today:
                statuses_pool = ['pending', 'confirmed']
            elif d == self.today:
                statuses_pool = ['confirmed', 'completed', 'in_progress']
            else:
                statuses_pool = ['completed', 'completed', 'completed', 'cancelled', 'no_show']

            slots = [time_cls(h, m) for h in range(9, 18) for m in (0, 30)]
            random.shuffle(slots)
            num_appts = random.randint(2, 4)

            for i in range(num_appts):
                specialist = random.choice(specialists)
                available = spec_services.get(specialist.pk) or services
                use_package = random.random() < 0.2
                client = random.choice(clients)
                start_time = slots[i % len(slots)]
                status = random.choice(statuses_pool)

                appt = Appointment.objects.create(
                    center=center, client=client, specialist=specialist,
                    date=d, start_time=start_time, status='pending', source='direct',
                )

                if use_package:
                    package = random.choice(packages)
                    appt.package = package
                    appt.save(update_fields=['package'])
                    total_minutes = package.total_duration
                else:
                    chosen = random.sample(available, k=min(len(available), random.choice([1, 1, 2])))
                    total_minutes = 0
                    for svc in chosen:
                        AppointmentService.objects.create(
                            appointment=appt, service=svc, unit_price=svc.price, specialist=specialist,
                        )
                        total_minutes += svc.duration

                from datetime import datetime as _dt
                end_dt = _dt.combine(d, start_time) + timedelta(minutes=total_minutes or 30)
                appt.end_time = end_dt.time()
                appt.save(update_fields=['end_time'])
                appt.recalculate_price()

                if status in ('confirmed', 'completed', 'in_progress'):
                    appt.status = 'confirmed'
                    appt.save(update_fields=['status'])  # fires signal -> creates draft invoice
                    if status != 'confirmed':
                        appt.status = status
                        appt.save(update_fields=['status'])
                else:
                    appt.status = status
                    appt.save(update_fields=['status'])

                appointments.append(appt)

        self.stdout.write(f'✓ Appointments: {len(appointments)}')
        return appointments

    def _create_appointment_invoices(self, center, appointments):
        billed = 0
        for appt in appointments:
            invoice = getattr(appt, 'invoice', None)
            if invoice is None:
                continue
            invoice.date = appt.date
            invoice.save(update_fields=['date'])

            if appt.package:
                InvoiceLine.objects.create(
                    invoice=invoice, description=appt.package.name, quantity=Decimal('1'),
                    unit_price=appt.package.price,
                )
            else:
                for a_service in appt.appointment_services.select_related('service'):
                    InvoiceLine.objects.create(
                        invoice=invoice, description=a_service.service.name, service=a_service.service,
                        specialist=appt.specialist, quantity=Decimal('1'), unit_price=a_service.unit_price,
                    )
            invoice.recalculate()

            if appt.status == 'completed':
                roll = random.random()
                method = random.choice(['cash', 'card_or_bank'])
                if roll < 0.7:
                    invoice.mark_paid(amount=invoice.total, method=method)
                    self._create_invoice_treasury_movement(invoice)
                elif roll < 0.9:
                    partial = (invoice.total * Decimal('0.5')).quantize(Decimal('0.01'))
                    invoice.mark_paid(amount=partial, method=method)
                    self._create_invoice_treasury_movement(invoice)
                # else: leave as draft/unpaid (invoiced but not yet settled)
            billed += 1
        self.stdout.write(f'✓ Invoices linked to appointments: {billed}')

    def _create_invoice_treasury_movement(self, invoice):
        if invoice.payment_method != 'cash':
            return
        treasury = Treasury.objects.filter(center=invoice.center).first()
        if not treasury:
            return
        ref = f'billing_{invoice.pk}'
        if TreasuryMovement.objects.filter(treasury__center=invoice.center, reference=ref).exists():
            return
        TreasuryMovement.objects.create(
            treasury=treasury, type='in', amount=invoice.paid_amount,
            reference=ref, notes=f'فاتورة {invoice.number}',
        )

    def _create_client_payment(self, center):
        """Top up one partially-paid invoice via a standalone ClientPayment record,
        mirroring apps/finance/views.py::client_payment_create."""
        invoice = Invoice.objects.filter(center=center, status='partial', client__isnull=False).first()
        if not invoice:
            return
        remaining = invoice.remaining
        payment = ClientPayment.objects.create(
            center=center, invoice=invoice, client=invoice.client,
            amount=remaining, method='cash', notes='دفعة إضافية لإغلاق الفاتورة',
        )
        invoice.paid_amount += remaining
        invoice.status = 'paid' if invoice.paid_amount >= invoice.total else 'partial'
        invoice.save(update_fields=['paid_amount', 'status'])

        treasury = Treasury.objects.filter(center=center).first()
        if treasury and payment.method == 'cash':
            TreasuryMovement.objects.create(
                treasury=treasury, type='in', amount=payment.amount,
                reference=f'payment_{payment.pk}', notes=f'دفعة فاتورة {invoice.number}',
            )
        self.stdout.write('✓ Client payment: 1 (settling a partial invoice)')

    def _create_pos_invoices(self, center, clients, specialists, services, products):
        settings_obj = Settings.objects.get(center=center)
        created = 0
        for day_offset in [0, 2, 4, 6]:
            d = self.week_start + timedelta(days=day_offset)
            if d > self.today:
                continue
            client = random.choice(clients)
            invoice = Invoice.objects.create(
                center=center, number=settings_obj.next_invoice_number(), client=client,
                date=d, payment_method='cash', status='draft',
            )
            for product in random.sample(products, k=2):
                qty = random.randint(1, 2)
                InvoiceLine.objects.create(
                    invoice=invoice, description=product.name, product=product,
                    quantity=Decimal(qty), unit_price=product.price,
                )
                StockMovement.objects.create(
                    center=center, product=product, change=Decimal(-qty),
                    type='sale', reference=f'invoice_{invoice.pk}',
                )
            invoice.recalculate()
            invoice.mark_paid(amount=invoice.total, method='cash')
            self._create_invoice_treasury_movement(invoice)
            created += 1
        self.stdout.write(f'✓ POS (retail) invoices: {created}')

    # ── finance ──────────────────────────────────────────────────────────
    def _create_expenses(self, center, treasury):
        data = [
            ('إيجار', '5000', 0), ('كهرباء ومياه', '800', 1),
            ('مستلزمات صالون', '650', 2), ('صيانة أجهزة', '300', 4),
        ]
        for category, amount, day_offset in data:
            expense = Expense.objects.create(
                center=center, category=category, amount=Decimal(amount), method='cash',
                date=self.week_start + timedelta(days=day_offset), treasury=treasury,
            )
            TreasuryMovement.objects.create(
                treasury=treasury, type='out', amount=expense.amount,
                reference=f'expense_{expense.pk}', notes=expense.category,
            )
        self.stdout.write(f'✓ Expenses: {len(data)}')

    def _create_advances_and_salaries(self, center, specialists, treasury):
        adv = Advance.objects.create(
            center=center, specialist=specialists[0], amount=Decimal('500'),
            date=self.week_start + timedelta(days=1), method='cash', treasury=treasury,
            notes='سلفة على الراتب',
        )
        adv._record_outflow()

        sp = SalaryPayment.objects.create(
            center=center, specialist=specialists[1],
            period_start=self.week_start.replace(day=1),
            period_end=self.week_start,
            base_salary=specialists[1].base_salary, bonus=Decimal('200'),
            method='cash', treasury=treasury, notes='راتب دفعة تجريبية',
        )
        sp.pay()
        self.stdout.write('✓ Advance: 1 (pending), Salary payment: 1 (paid)')

    def _create_user_advances_and_salaries(self, center, users, treasury):
        reception, staff_user = users[1], users[2]
        adv = UserAdvance.objects.create(
            center=center, user=reception, amount=Decimal('300'),
            date=self.week_start + timedelta(days=2), method='cash', treasury=treasury,
            notes='سلفة موظف',
        )
        adv._record_outflow()

        sp = UserSalaryPayment.objects.create(
            center=center, user=staff_user,
            period_start=self.week_start.replace(day=1), period_end=self.week_start,
            base_salary=staff_user.base_salary, method='cash', treasury=treasury,
        )
        sp.pay()
        self.stdout.write('✓ User advance: 1 (pending), User salary payment: 1 (paid)')

    def _create_incentives(self, center, specialists, users, treasury):
        bonus = Incentive.objects.create(
            center=center, person_type='specialist', specialist=specialists[2],
            type='bonus', amount=Decimal('150'), description='أداء متميز هذا الأسبوع',
            payout='immediate', method='cash', treasury=treasury, date=self.today,
        )
        bonus._record_outflow()
        bonus.status = 'paid'
        bonus.save(update_fields=['status'])

        deduction = Incentive.objects.create(
            center=center, person_type='user', user=users[1],
            type='deduction', amount=Decimal('50'), description='تأخير عن الدوام',
            payout='with_salary', method='cash', date=self.today, status='pending',
        )
        self.stdout.write('✓ Incentives: 2 (bonus paid, deduction pending)')

    # ── store ────────────────────────────────────────────────────────────
    def _create_online_bookings(self, center, services, clients):
        data = [
            (clients[3], services[0], self.week_start + timedelta(days=2), 'new'),
            (clients[4], services[3], self.week_start + timedelta(days=3), 'confirmed'),
            (None, services[5], self.week_start + timedelta(days=5), 'new'),
        ]
        for client, service, pref_date, status in data:
            OnlineBooking.objects.create(
                center=center,
                client_name=client.name if client else 'زائرة جديدة',
                client_phone=client.phone if client else '+249911999999',
                client=client, service=service, preferred_date=pref_date,
                preferred_time=time_cls(11, 0), status=status,
            )
        self.stdout.write(f'✓ Online bookings: {len(data)}')

    def _create_store_orders(self, center, products, clients):
        statuses = ['pending', 'confirmed', 'delivered', 'cancelled']
        for i, status in enumerate(statuses):
            client = clients[i]
            order = StoreOrder.objects.create(
                center=center, client=client, client_name=client.name, client_phone=client.phone,
                client_address='الخرطوم، حي الرياض', status=status,
            )
            for product in random.sample(products, k=2):
                qty = random.randint(1, 3)
                StoreOrderItem.objects.create(order=order, product=product, quantity=qty, unit_price=product.price)
            order.recalculate()
            # auto_now_add ignores create()-time values; backdate via update() so orders spread across the week
            StoreOrder.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(days=(6 - i)))
        self.stdout.write(f'✓ Store orders: {len(statuses)}')

    # ── notifications / support ──────────────────────────────────────────
    def _create_notifications(self, center):
        data = [
            ('booking_new', 'حجز جديد عبر المتجر', 'حجز جديد بانتظار التأكيد'),
            ('order_new', 'طلب متجر جديد', 'طلب شراء منتجات جديد'),
            ('stock_low', 'مخزون منخفض', 'أحد المنتجات أوشك على النفاد'),
        ]
        for type_, title, body in data:
            Notification.objects.create(center=center, type=type_, title=title, body=body)
        self.stdout.write(f'✓ Notifications: {len(data)}')

    def _create_support_ticket(self, center, users):
        owner = users[0]
        ticket = SupportTicket.objects.create(
            center=center, created_by=owner, subject='استفسار عن ميزة نقاط الولاء',
            description='كيف يمكن تفعيل نقاط الولاء لجميع العملاء بأثر رجعي؟',
            category='feature', priority='medium', status='in_progress',
        )
        SupportMessage.objects.create(ticket=ticket, sender=owner, sender_type='center', body='هل يمكن المساعدة في هذا الأمر؟')
        SupportMessage.objects.create(ticket=ticket, sender=None, sender_type='admin', body='مرحباً، سنقوم بمراجعة الطلب والرد خلال 24 ساعة.')
        ticket.last_reply_at = timezone.now()
        ticket.save(update_fields=['last_reply_at'])
        self.stdout.write('✓ Support ticket with 2 messages')

    def _create_center_backup(self, center):
        CenterBackup.objects.create(
            center=center, filename=f'{center.slug}_backup_demo.sql', file_path=f'/backups/{center.slug}_backup_demo.sql',
            file_size=204800, backup_type='manual', status='completed', notes='نسخة تجريبية للعرض',
        )
        self.stdout.write('✓ Center backup record: 1')
