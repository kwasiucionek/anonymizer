from django.contrib import admin, messages
from django.utils.html import format_html

from .models import Case, Document
from .tasks import enqueue_document, request_stop

STATUS_COLORS = {
    Document.Status.PENDING: "#9A6B00",
    Document.Status.PROCESSING: "#9A6B00",
    Document.Status.DONE: "#1E7A46",
    Document.Status.FAILED: "#A3312F",
    Document.Status.CANCELLED: "#5C6470",
}


@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ["case_id", "person_count_display", "created"]
    search_fields = ["case_id"]  # wymagane przez autocomplete w DocumentAdmin
    readonly_fields = ["persons_cache", "created", "modified"]
    fieldsets = [
        (None, {"fields": ["case_id", "description"]}),
        ("Cache osób", {"fields": ["persons_cache"], "classes": ["collapse"]}),
        ("Znaczniki czasu", {"fields": ["created", "modified"]}),
    ]

    @admin.display(description="Osoby w cache")
    def person_count_display(self, obj):
        return obj.person_count


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = [
        "original_name",
        "case",
        "status_badge",
        "entity_count",
        "person_count",
        "duration",
        "created",
    ]
    list_select_related = ["case"]
    list_filter = ["status", "case"]
    search_fields = ["original_name", "case__case_id"]
    autocomplete_fields = ["case"]
    date_hierarchy = "created"
    list_per_page = 25
    readonly_fields = [
        "status",
        "error_message",
        "entity_count",
        "person_count",
        "duration",
        "finished_at",
        "task_id",
        "cancel_requested",
        "created",
        "modified",
    ]
    actions = ["requeue", "stop_selected"]

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return format_html(
            '<b style="color:{}">{}</b>',
            STATUS_COLORS.get(obj.status, "#5C6470"),
            obj.get_status_display(),
        )

    @admin.action(description="Zatrzymaj zaznaczone dokumenty")
    def stop_selected(self, request, queryset):
        if not request.user.has_perm("dokumenty.change_document"):
            self.message_user(request, "Brak uprawnień.", messages.ERROR)
            return
        cancelled = stopping = 0
        for document in queryset:
            outcome = request_stop(document)
            if outcome == "cancelled":
                cancelled += 1
            elif outcome == "stopping":
                stopping += 1
        self.message_user(
            request,
            f"Anulowano z kolejki: {cancelled}, przerwanie w toku: {stopping}.",
            messages.SUCCESS,
        )

    @admin.action(description="Przetwórz ponownie zaznaczone dokumenty")
    def requeue(self, request, queryset):
        if not request.user.has_perm("dokumenty.change_document"):
            self.message_user(request, "Brak uprawnień.", messages.ERROR)
            return
        count = 0
        for document in queryset.exclude(status=Document.Status.PROCESSING):
            document.reset_for_retry()
            enqueue_document(document)
            count += 1
        self.message_user(
            request, f"Zakolejkowano ponownie {count} dokument(ów).", messages.SUCCESS
        )
