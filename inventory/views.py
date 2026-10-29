import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import CharField, Exists, F, OuterRef, Value
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.permissions import Perm, role_required

from . import services
from .excel import XLSX_CONTENT_TYPE, build_stock_workbook
from .forms import HistoryFilterForm, IssueForm, ProductForm, ReceiveForm
from .models import Operation, OperationType, Product
from .numbers import format_number, format_quantity, format_totals
from .services import DomainError
from .stock import stock_report

HISTORY_PAGE_SIZE = 50


def _wants_archived(request) -> bool:
    return request.GET.get("archived") == "1"


# --- stock ----------------------------------------------------------------------


@role_required(Perm.VIEW_STOCK)
@require_GET
def stock(request):
    report = stock_report(request.user, query=request.GET.get("q", ""), archived=_wants_archived(request))
    export_params = {"q": report.query} if report.query else {}
    if report.archived:
        export_params["archived"] = "1"
    return render(
        request,
        "inventory/stock.html",
        {"report": report, "totals_text": format_totals(report.totals), "export_query": urlencode(export_params)},
    )


@role_required(Perm.EXPORT_STOCK)
@require_GET
def export_stock(request):
    report = stock_report(request.user, query=request.GET.get("q", ""), archived=_wants_archived(request))
    response = HttpResponse(build_stock_workbook(report), content_type=XLSX_CONTENT_TYPE)
    filename = f"stock_{timezone.localdate():%Y-%m-%d}.xlsx"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# --- receive / issue --------------------------------------------------------------


def _form_key(form) -> str:
    """Keep the key across validation errors (a retry stays idempotent); otherwise mint one."""
    if form.is_bound:
        try:
            return str(uuid.UUID(str(form.data.get("idempotency_key", ""))))
        except ValueError:
            pass
    return str(uuid.uuid4())


def _announce(request, result: services.OperationResult) -> None:
    op = result.operation
    params = {"name": op.product.name, "qty": format_quantity(op.quantity, op.product.unit)}
    if not result.created:
        messages.info(request, _("Операция уже сохранена: %(name)s — %(qty)s") % params)
    elif op.type == OperationType.IN:
        messages.success(request, _("Приход сохранён: %(name)s — %(qty)s") % params)
    else:
        params["object"] = op.object_name
        messages.success(request, _("Выдача сохранена: %(name)s — %(qty)s → %(object)s") % params)


def _initial_product(request):
    raw = request.GET.get("product", "")
    return {"product": int(raw)} if raw.isdigit() and len(raw) < 18 else {}


def _movement(request, form_class):
    if request.method == "POST":
        form = form_class(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                if form_class.operation_type == OperationType.IN:
                    result = services.receive(
                        request.user, data["product"], data["quantity"], data["comment"],
                        key=data["idempotency_key"],
                    )
                else:
                    result = services.issue(
                        request.user, data["product"], data["quantity"], data["object_name"],
                        data["comment"], key=data["idempotency_key"],
                    )
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                _announce(request, result)
                return redirect("inventory:stock")
    else:
        form = form_class(initial=_initial_product(request))

    is_issue = form_class.operation_type == OperationType.OUT
    return render(
        request,
        "inventory/movement.html",
        {
            "form": form,
            "form_key": _form_key(form),
            "is_issue": is_issue,
            "title": _("Выдача") if is_issue else _("Приход"),
        },
    )


@role_required(Perm.MOVE_STOCK)
@require_http_methods(["GET", "POST"])
def receive(request):
    return _movement(request, ReceiveForm)


@role_required(Perm.MOVE_STOCK)
@require_http_methods(["GET", "POST"])
def issue(request):
    return _movement(request, IssueForm)


# --- products ---------------------------------------------------------------------


@role_required(Perm.MANAGE_PRODUCTS)
@require_GET
def products(request):
    items = Product.objects.annotate(
        has_operations=Exists(Operation.objects.filter(product=OuterRef("pk")))
    ).order_by("is_archived", "name_key", "id")
    return render(request, "inventory/products.html", {"products": items})


def _product_form_page(request, form, product=None):
    return render(request, "inventory/product_form.html", {"form": form, "product": product})


@role_required(Perm.MANAGE_PRODUCTS)
@require_http_methods(["GET", "POST"])
def product_create(request):
    form = ProductForm(request.POST if request.method == "POST" else None, initial={"unit": "pcs", "currency": "UZS"})
    if form.is_valid():
        try:
            product = services.create_product(request.user, **form.cleaned_data)
        except DomainError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, _("Товар добавлен: %(name)s") % {"name": product.name})
            return redirect("inventory:products")
    return _product_form_page(request, form)


@role_required(Perm.MANAGE_PRODUCTS)
@require_http_methods(["GET", "POST"])
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    unit_locked = product.operations.exists()
    initial = {
        "name": product.name,
        "unit": product.unit,
        "price": format_number(product.price, 2),
        "currency": product.currency,
    }
    form = ProductForm(
        request.POST if request.method == "POST" else None, initial=initial, unit_locked=unit_locked
    )
    if form.is_valid():
        try:
            product = services.update_product(request.user, product, **form.cleaned_data)
        except DomainError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, _("Товар сохранён: %(name)s") % {"name": product.name})
            return redirect("inventory:products")
    return _product_form_page(request, form, product)


def _product_action(request, pk, action, success_message):
    product = get_object_or_404(Product, pk=pk)
    try:
        action(request.user, product)
    except DomainError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, success_message % {"name": product.name})
    return redirect("inventory:products")


@role_required(Perm.MANAGE_PRODUCTS)
@require_POST
def product_archive(request, pk):
    return _product_action(request, pk, services.archive_product, _("Товар отправлен в архив: %(name)s"))


@role_required(Perm.MANAGE_PRODUCTS)
@require_POST
def product_restore(request, pk):
    return _product_action(request, pk, services.restore_product, _("Товар возвращён из архива: %(name)s"))


@role_required(Perm.MANAGE_PRODUCTS)
@require_POST
def product_delete(request, pk):
    return _product_action(request, pk, services.delete_product, _("Товар удалён: %(name)s"))


# --- history ----------------------------------------------------------------------


def _history_events(base):
    """Operations and their cancellations as one timeline, newest first, paginated in SQL."""
    created = (
        base.order_by()
        .annotate(event_at=F("created_at"), kind=Value("operation", output_field=CharField()))
        .values("id", "event_at", "kind")
    )
    cancelled = (
        base.filter(cancelled_at__isnull=False)
        .order_by()
        .annotate(event_at=F("cancelled_at"), kind=Value("cancel", output_field=CharField()))
        .values("id", "event_at", "kind")
    )
    # "kind" breaks exact-timestamp ties: an operation's cancel ("cancel") lists above it.
    return created.union(cancelled, all=True).order_by("-event_at", "-id", "kind")


@role_required(Perm.VIEW_HISTORY)
@require_GET
def history(request):
    form = HistoryFilterForm(request.GET)
    base = Operation.objects.all()
    filters = {}
    if form.is_valid():
        if form.cleaned_data["product"] is not None:
            base = base.filter(product=form.cleaned_data["product"])
            filters["product"] = form.cleaned_data["product"].pk
        if form.cleaned_data["type"]:
            base = base.filter(type=form.cleaned_data["type"])
            filters["type"] = form.cleaned_data["type"]
    else:
        form = HistoryFilterForm()

    page = Paginator(_history_events(base), HISTORY_PAGE_SIZE).get_page(request.GET.get("page"))
    rows = list(page.object_list)
    operations = Operation.objects.select_related("product", "created_by", "cancelled_by").in_bulk(
        [row["id"] for row in rows]
    )
    events = [
        {"kind": row["kind"], "at": row["event_at"], "op": operations[row["id"]]}
        for row in rows
        if row["id"] in operations
    ]
    return render(
        request,
        "inventory/history.html",
        {"form": form, "page": page, "events": events, "filter_query": urlencode(filters)},
    )


@role_required(Perm.CANCEL_OPERATION)
@require_http_methods(["GET", "POST"])
def cancel_operation(request, pk):
    op = get_object_or_404(
        Operation.objects.select_related("product", "created_by", "cancelled_by"), pk=pk
    )
    error = ""
    if request.method == "POST":
        try:
            op = services.cancel(request.user, op)
        except DomainError as exc:
            error = exc.message
            op.refresh_from_db()
        else:
            messages.success(
                request,
                _("Операция отменена: %(type)s — %(name)s — %(qty)s")
                % {
                    "type": op.get_type_display(),
                    "name": op.product.name,
                    "qty": format_quantity(op.quantity, op.product.unit),
                },
            )
            return redirect("inventory:history")
    return render(request, "inventory/cancel.html", {"op": op, "error": error})
