

import frappe
import requests
import base64
import re

TICKET_SITE     = "ticket.nextoraerp.com"
TICKET_URL      = f"https://{TICKET_SITE}"
TICKET_USER     = "ticketuser@gmaill.com"
TICKET_PASSWORD = "ticketuser@123"

SUPPORT_USER     = "ticket_support"
SUPPORT_PASSWORD = "support@zinple"


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
def safe_log(title, message):
    try:
        frappe.log_error(
            str(message)[:5000],
            str(title)[:140]
        )
    except Exception:
        pass


def get_valid_email(raised_by):
    if raised_by and "@" in raised_by:
        return raised_by
    email = frappe.db.get_value("User", frappe.session.user, "email")
    if email and "@" in email:
        return email
    return None


def get_file_base64(file_url):
    try:
        if file_url.startswith("/private"):
            file_path = frappe.get_site_path() + file_url
        elif file_url.startswith("/files"):
            file_path = frappe.get_site_path("public") + file_url
        else:
            return None, None

        with open(file_path, "rb") as f:
            file_data = f.read()

        file_name = file_url.split("/")[-1]
        encoded   = base64.b64encode(file_data).decode("utf-8")
        return file_name, encoded

    except Exception:
        safe_log("File Read Error", frappe.get_traceback())
        return None, None


def login_to_site(url, username, password):
    try:
        session   = requests.Session()
        login_res = session.post(
            f"{url}/api/method/login",
            data={"usr": username, "pwd": password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=120
        )
        resp = login_res.json()
        if resp.get("message") != "Logged In":
            safe_log("Login Failed", f"Could not login to {url}: {str(resp)[:300]}")
            return None, None

        csrf_token = session.cookies.get("csrf_token", "")
        return session, csrf_token

    except Exception:
        safe_log("Login Exception", frappe.get_traceback())
        return None, None


def upload_file(session, csrf_token, site_url, file_name, file_content, doctype, docname):
    try:
        res = session.post(
            f"{site_url}/api/method/upload_file",
            data={
                "is_private": 0,
                "doctype"   : doctype,
                "docname"   : docname,
                "fieldname" : "attachment",
            },
            files={"file": (file_name, file_content)},
            headers={"X-Frappe-CSRF-Token": csrf_token},
            timeout=300
        )
        result = res.json()
        if result.get("message"):
            return result["message"].get("file_url")
        safe_log("File Upload Failed", str(result)[:500])
        return None
    except Exception:
        safe_log("File Upload Exception", frappe.get_traceback())
        return None


def upload_attachments(session, csrf_token, site_url, local_doc_name, remote_docname):
    attachments = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": "Issue",
            "attached_to_name"   : local_doc_name
        },
        fields=["file_name", "file_url", "is_private"]
    )

    uploaded_map = {}

    for att in attachments:
        file_name, file_base64 = get_file_base64(att.file_url)
        if not file_name or not file_base64:
            continue

        file_content = base64.b64decode(file_base64)
        remote_url   = upload_file(
            session, csrf_token, site_url,
            file_name, file_content, "Issue", remote_docname
        )
        if remote_url:
            uploaded_map[att.file_url] = remote_url

    return uploaded_map


def fix_inline_images(description, session, csrf_token, site_url, remote_docname):
    if not description:
        return description

    pattern = r'(src|href)=["\']((\/private\/files\/|\/files\/)[^"\']+)["\']'
    matches  = re.findall(pattern, description)

    for attr, file_url, _ in matches:
        file_name, file_base64 = get_file_base64(file_url)
        if not file_name or not file_base64:
            continue

        file_content = base64.b64decode(file_base64)
        remote_url   = upload_file(
            session, csrf_token, site_url,
            file_name, file_content, "Issue", remote_docname
        )
        if remote_url:
            description = description.replace(
                file_url,
                f"{site_url}{remote_url}"
            )

    return description


# ─────────────────────────────────────────────────────────────
# FRAPPE HOOKS
# ─────────────────────────────────────────────────────────────
def after_insert(doc, method):
    # Forward sync: client site → ticket.nextoraerp.com
    # Skip if this issue was itself created by the ticket portal
    if doc.get("custom_by_ticket"):
        return
    sync_to_ticket_portal(doc)


def on_update(doc, method):
    # Reverse sync: ticket.nextoraerp.com → client site
    # Only runs for issues that came from a client site
    if getattr(doc, "_syncing", False):
        return

    doc._syncing = True

    if (
        doc.get("custom_by_ticket") == 1
        and doc.get("custom_site_name")
        and doc.get("custom_issue_id")
    ):
        reverse_sync_to_source(doc)


# ─────────────────────────────────────────────────────────────
# FORWARD SYNC: client site → ticket.nextoraerp.com
# ─────────────────────────────────────────────────────────────
def sync_to_ticket_portal(doc):
    try:
        session, csrf_token = login_to_site(TICKET_URL, TICKET_USER, TICKET_PASSWORD)
        if not session:
            return

        payload = {
            "subject"          : doc.get("subject"),
            "raised_by"        : get_valid_email(doc.get("raised_by")),
            "description"      : doc.get("description") or "",
            "status"           : doc.get("status"),
            "priority"         : doc.get("priority"),
            "issue_type"       : doc.get("issue_type"),
            "custom_site_name" : frappe.local.site,
            "custom_issue_id"  : doc.name,
            "custom_by_ticket" : 1,
        }

        payload = {k: v for k, v in payload.items() if v is not None and v != ""}

        res    = session.post(
            f"{TICKET_URL}/api/resource/Issue",
            json=payload,
            headers={"X-Frappe-CSRF-Token": csrf_token},
            timeout=300
        )
        result = res.json()

        if not result.get("data"):
            safe_log("Ticket Sync Failed", str(result)[:500])
            return

        remote_doc_name = result["data"].get("name")

        uploaded_map = upload_attachments(
            session, csrf_token, TICKET_URL, doc.name, remote_doc_name
        )

        updated_description = fix_inline_images(
            doc.get("description"), session, csrf_token, TICKET_URL, remote_doc_name
        )

        if updated_description != doc.get("description"):
            session.put(
                f"{TICKET_URL}/api/resource/Issue/{remote_doc_name}",
                json={"description": updated_description},
                headers={"X-Frappe-CSRF-Token": csrf_token},
                timeout=300
            )

        frappe.db.set_value("Issue", doc.name, "ticket_portal_id", remote_doc_name)
        frappe.db.commit()

        frappe.msgprint(
            f"Issue synced to ticket portal.<br>"
            f"Ticket ID: <b>{remote_doc_name}</b><br>"
            f"Attachments synced: <b>{len(uploaded_map)}</b>",
            title="Ticket Synced",
            indicator="green"
        )

        safe_log(
            "Ticket Sync Success",
            f"Remote: {remote_doc_name} | Local: {doc.name} | Attachments: {len(uploaded_map)}"
        )

    except Exception:
        safe_log("Ticket Sync Exception", frappe.get_traceback())


# ─────────────────────────────────────────────────────────────
# REVERSE SYNC: ticket.nextoraerp.com → client site
# ─────────────────────────────────────────────────────────────
def reverse_sync_to_source(doc):
    try:
        # Prevent infinite loop
        if doc.get("custom_is_syncing"):
            frappe.db.set_value("Issue", doc.name, "custom_is_syncing", 0)
            frappe.db.commit()
            return

        source_site     = doc.get("custom_site_name")
        custom_issue_id = doc.get("custom_issue_id")
        source_url      = f"https://{source_site}"

        # Check only relevant fields changed
        changed = (
            doc.has_value_changed("status")
            or doc.has_value_changed("description")
            or doc.has_value_changed("resolution_details")
            or doc.has_value_changed("priority")
            or doc.has_value_changed("first_responded_on")
        )

        if not changed:
            return

        # Login to client site
        session, csrf_token = login_to_site(source_url, SUPPORT_USER, SUPPORT_PASSWORD)
        if not session:
            return

        # Verify the issue exists on the client site before updating
        check_res = session.get(
            f"{source_url}/api/resource/Issue/{custom_issue_id}",
            headers={"X-Frappe-CSRF-Token": csrf_token},
            timeout=60
        )
        try:
            check_result = check_res.json()
        except Exception:
            check_result = {}

        if not check_result.get("data"):
            safe_log(
                "Reverse Sync Not Found",
                f"Issue {custom_issue_id} not found on {source_site}"
            )
            return

        # Upload attachments to client site
        uploaded_map = upload_attachments(
            session, csrf_token, source_url, doc.name, custom_issue_id
        )

        # Fix description URLs
        description = doc.description or ""

        for old_url, new_url in uploaded_map.items():
            description = description.replace(old_url, f"{source_url}{new_url}")

        description = description.replace(
            f"{TICKET_URL}/private/files/",
            f"{source_url}/files/"
        )
        description = description.replace(
            f"{TICKET_URL}/files/",
            f"{source_url}/files/"
        )

        # Build payload with only changed fields
        payload = {"custom_is_syncing": 1}

        if doc.has_value_changed("status"):
            payload["status"] = doc.status

        if doc.has_value_changed("description"):
            payload["description"] = description

        if doc.has_value_changed("resolution_details"):
            payload["resolution_details"] = doc.resolution_details

        if doc.has_value_changed("priority"):
            payload["priority"] = doc.priority

        if doc.has_value_changed("first_responded_on"):
            payload["first_responded_on"] = doc.first_responded_on

        # Update issue on client site
        update_res = session.put(
            f"{source_url}/api/resource/Issue/{custom_issue_id}",
            json=payload,
            headers={"X-Frappe-CSRF-Token": csrf_token},
            timeout=300
        )

        try:
            result = update_res.json()
        except Exception:
            result = {"response": update_res.text[:500]}

        if not result.get("data"):
            safe_log("Reverse Sync Failed", str(result)[:500])
        else:
            safe_log(
                "Reverse Sync Success",
                f"{custom_issue_id} updated on {source_site}"
            )

    except Exception:
        safe_log("Reverse Sync Exception", frappe.get_traceback())