import frappe
import requests
import base64
import re


def after_insert(doc, method):
    pass


def on_update(doc, method):
    if getattr(doc, "_syncing", False):
        return

    doc._syncing = True

    try:
        # Reverse sync: ticket portal → source site
        if doc.get("custom_by_ticket") == 1 and doc.get("custom_site_name"):
            reverse_sync_to_source(doc)

        # Forward sync: source site → ticket portal
        else:
            sync_to_ticket_portal(doc)

    except Exception:
        frappe.log_error(
            title="Issue Sync Error",
            message=frappe.get_traceback()
        )

    finally:
        doc._syncing = False


def get_valid_email(raised_by):
    if raised_by and "@" in raised_by:
        return raised_by

    email = frappe.db.get_value(
        "User",
        frappe.session.user,
        "email"
    )

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
        encoded = base64.b64encode(file_data).decode("utf-8")

        return file_name, encoded

    except Exception:
        frappe.log_error(
            title="File Read Error",
            message=frappe.get_traceback()
        )
        return None, None


def upload_file_to_portal(session, file_name, file_content, doctype, docname):
    try:
        response = session.post(
            "https://ticket.nextoraerp.com/api/method/upload_file",
            data={
                "is_private": 0,
                "doctype": doctype,
                "docname": docname,
                "fieldname": "attachment",
            },
            files={
                "file": (
                    file_name,
                    file_content
                )
            }
        )

        result = response.json()

        if result.get("message"):
            return result["message"].get("file_url")

        frappe.log_error(
            title="File Upload Response Error",
            message=str(result)
        )

        return None

    except Exception:
        frappe.log_error(
            title="File Upload Error",
            message=frappe.get_traceback()
        )
        return None


def fix_description_links(description, session, remote_doc_name):
    if not description:
        return description

    pattern = r'(src|href)=["\']((\/private\/files\/|\/files\/)[^"\']+)["\']'
    matches = re.findall(pattern, description)

    for _, file_url, _ in matches:

        file_name, file_base64 = get_file_base64(file_url)

        if file_name and file_base64:
            file_content = base64.b64decode(file_base64)

            remote_url = upload_file_to_portal(
                session,
                file_name,
                file_content,
                "Issue",
                remote_doc_name
            )

            if remote_url:
                description = description.replace(
                    file_url,
                    f"https://ticket.nextoraerp.com{remote_url}"
                )

    return description


# ------------------------------------------------
# FORWARD SYNC: your site → ticket.nextoraerp.com
# ------------------------------------------------
def sync_to_ticket_portal(doc):
    try:
        session = requests.Session()

        # Login
        login_res = session.post(
            "https://ticket.nextoraerp.com/api/method/login",
            data={
                "usr": "ticketuser@gmaill.com",
                "pwd": "ticketuser@123"
            },
            headers={
                "Content-Type":
                "application/x-www-form-urlencoded"
            }
        )

        login_json = login_res.json()

        if login_json.get("message") != "Logged In":
            frappe.log_error(
                title="Ticket Sync Login Failed",
                message=str(login_json)
            )
            return

        # Fetch attachments
        attachments = frappe.get_all(
            "File",
            filters={
                "attached_to_doctype": "Issue",
                "attached_to_name": doc.name
            },
            fields=[
                "file_name",
                "file_url",
                "is_private"
            ]
        )

        frappe.log_error(
            title="Attachment Debug",
            message=f"Found {len(attachments)} attachments for {doc.name}"
        )

        payload = {
            "subject": doc.get("subject"),
            "raised_by": get_valid_email(doc.get("raised_by")),
            "description": doc.get("description") or "",
            "resolution_details": doc.get("resolution_details"),
            "via_customer_portal": doc.get("via_customer_portal"),
            "custom_site_name": frappe.local.site,
            "issue_type": doc.get("issue_type"),
            "priority": doc.get("priority"),
            "custom_issue_id": doc.get("name"),
            "custom_by_ticket": 1,
        }

        payload = {
            k: v for k, v in payload.items()
            if v not in [None, ""]
        }

        # Create issue
        response = session.post(
            "https://ticket.nextoraerp.com/api/resource/Issue",
            json=payload
        )

        result = response.json()

        if not result.get("data"):
            frappe.log_error(
                title="Ticket Sync Failed",
                message=str(result)
            )
            return

        remote_doc_name = result["data"].get("name")

        frappe.log_error(
            title="Ticket Sync",
            message=f"Remote issue created: {remote_doc_name}"
        )

        # Upload attachments
        uploaded_files = []

        for att in attachments:
            file_name, file_base64 = get_file_base64(att.file_url)

            if file_name and file_base64:
                file_content = base64.b64decode(file_base64)

                remote_url = upload_file_to_portal(
                    session,
                    file_name,
                    file_content,
                    "Issue",
                    remote_doc_name
                )

                if remote_url:
                    uploaded_files.append(file_name)

        # Fix inline images
        updated_description = fix_description_links(
            doc.get("description"),
            session,
            remote_doc_name
        )

        # Update description
        if updated_description != doc.get("description"):
            session.put(
                f"https://ticket.nextoraerp.com/api/resource/Issue/{remote_doc_name}",
                json={"description": updated_description}
            )

        # Save remote ticket ID
        frappe.db.set_value(
            "Issue",
            doc.name,
            "ticket_portal_id",
            remote_doc_name
        )

        frappe.db.commit()

    except Exception:
        frappe.log_error(
            title="Ticket Sync Exception",
            message=frappe.get_traceback()
        )


# ------------------------------------------------
# REVERSE SYNC: ticket.nextoraerp.com → source
# ------------------------------------------------
def reverse_sync_to_source(doc):
    source_site = doc.get("custom_site_name")

    if not source_site:
        frappe.log_error(
            title="Reverse Sync Skipped",
            message="No source site found"
        )
        return

    try:
        http = requests.Session()

        # Login
        login_res = http.post(
            f"https://{source_site}/api/method/login",
            data={
                "usr": "ticket_support",
                "pwd": "support@zinple"
            },
            headers={
                "Content-Type":
                "application/x-www-form-urlencoded"
            }
        )

        login_data = login_res.json()

        frappe.log_error(
            title="Reverse Sync Login Response",
            message=str(login_data)
        )

        if login_data.get("message") != "Logged In":
            frappe.log_error(
                title="Reverse Sync Login Failed",
                message=str(login_data)
            )
            return

        custom_issue_id = doc.get("custom_issue_id")
        local_issue_name = None

        if custom_issue_id:
            search_res = http.get(
                f"https://{source_site}/api/resource/Issue/{custom_issue_id}"
            )

            issue_data = search_res.json().get("data")

            if issue_data:
                local_issue_name = issue_data.get("name")

        else:
            search_res = http.get(
                f"https://{source_site}/api/resource/Issue",
                params={
                    "filters": f'[["ticket_portal_id","=","{doc.name}"]]',
                    "fields": '["name"]',
                    "limit": 1
                }
            )

            issues = search_res.json().get("data", [])

            if issues:
                local_issue_name = issues[0]["name"]

        frappe.log_error(
            title="Reverse Sync Local Issue",
            message=str(local_issue_name)
        )

        if not local_issue_name:
            frappe.log_error(
                title="Reverse Sync Not Found",
                message=f"No issue found on {source_site} for ticket {doc.name}"
            )
            return

        payload = {
            "status": doc.get("status"),
            "description": doc.get("description"),
        }

        payload = {
            k: v for k, v in payload.items()
            if v not in [None, ""]
        }

        frappe.log_error(
            title="Reverse Sync Payload",
            message=str(payload)
        )

        update_res = http.put(
            f"https://{source_site}/api/resource/Issue/{local_issue_name}",
            json=payload
        )

        result = update_res.json()

        frappe.log_error(
            title="Reverse Sync Update Response",
            message=str(result)
        )

    except Exception:
        frappe.log_error(
            title="Reverse Sync Exception",
            message=frappe.get_traceback()
        )