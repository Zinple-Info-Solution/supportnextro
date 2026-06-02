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

    # If custom_by_ticket = 1 and custom_site_name exists
    # this doc is on ticket.nextoraerp.com — sync back to source site
    if doc.get("custom_by_ticket") == 1 and doc.get("custom_site_name"):
        reverse_sync_to_source(doc)
    else:
        # Normal forward sync — your site → ticket.nextoraerp.com
        sync_to_ticket_portal(doc)

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
        frappe.log_error(frappe.get_traceback(), "File Read Error")
        return None, None

def upload_file_to_portal(session, file_name, file_content, doctype, docname):
    try:
        response = session.post(
            "https://ticket.nextoraerp.com/api/method/upload_file",
            data={
                "is_private": 0,
                "doctype"   : doctype,
                "docname"   : docname,
                "fieldname" : "attachment",
            },
            files={"file": (file_name, file_content)}
        )
        result = response.json()
        if result.get("message"):
            return result["message"].get("file_url")
        frappe.log_error(str(result), "File Upload Response Error")
        return None
    except Exception:
        frappe.log_error(frappe.get_traceback(), "File Upload Error")
        return None

def fix_description_links(description, session, remote_doc_name):
    if not description:
        return description

    pattern = r'(src|href)=["\']((\/private\/files\/|\/files\/)[^"\']+)["\']'
    matches  = re.findall(pattern, description)

    for attr, file_url, _ in matches:
        file_name, file_base64 = get_file_base64(file_url)
        if file_name and file_base64:
            file_content = base64.b64decode(file_base64)
            remote_url   = upload_file_to_portal(
                session, file_name, file_content, "Issue", remote_doc_name
            )
            if remote_url:
                description = description.replace(
                    file_url,
                    f"https://ticket.nextoraerp.com{remote_url}"
                )
    return description

# ─────────────────────────────────────────────
# FORWARD SYNC: your site → ticket.nextoraerp.com
# ─────────────────────────────────────────────
def sync_to_ticket_portal(doc):
    try:
        # Step 1: Login
        session = requests.Session()
        login_res = session.post(
            "https://ticket.nextoraerp.com/api/method/login",
            data={
                "usr": "ticketuser@gmaill.com",
                "pwd": "ticketuser@123"
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

        if login_res.json().get("message") != "Logged In":
            frappe.log_error("Login failed", "Ticket Sync")
            return

        # Step 2: Fetch attachments
        attachments = frappe.get_all(
            "File",
            filters={
                "attached_to_doctype": "Issue",
                "attached_to_name"   : doc.name
            },
            fields=["file_name", "file_url", "is_private"]
        )

        frappe.log_error(
            f"Found {len(attachments)} attachments for {doc.name}",
            "Attachment Debug"
        )

        # Step 3: Build payload
        payload = {
            "subject"            : doc.get("subject"),
            "raised_by"          : get_valid_email(doc.get("raised_by")),
            "description"        : doc.get("description") or "",
            "resolution_details" : doc.get("resolution_details"),
            "via_customer_portal": doc.get("via_customer_portal"),
            "custom_site_name"   : frappe.local.site,
            "issue_type"         : doc.get("issue_type"),
            "priority"           : doc.get("priority"),
            "custom_issue_id"    : doc.get("name"),
            "custom_by_ticket"   : 1,
        }

        payload = {k: v for k, v in payload.items() if v is not None and v != ""}

        # Step 4: Create issue on remote
        response = session.post(
            "https://ticket.nextoraerp.com/api/resource/Issue",
            json=payload
        )

        result = response.json()

        if not result.get("data"):
            frappe.log_error(str(result), "Ticket Sync Failed")
            return

        remote_doc_name = result["data"].get("name")
        frappe.log_error(f"Remote issue created: {remote_doc_name}", "Ticket Sync")

        # Step 5: Upload attachments
        uploaded_files = []
        for att in attachments:
            file_name, file_base64 = get_file_base64(att.file_url)
            if file_name and file_base64:
                file_content = base64.b64decode(file_base64)
                remote_url   = upload_file_to_portal(
                    session, file_name, file_content, "Issue", remote_doc_name
                )
                if remote_url:
                    uploaded_files.append(file_name)

        # Step 6: Fix inline images in description
        updated_description = fix_description_links(
            doc.get("description"), session, remote_doc_name
        )

        # Step 7: Update remote if description had inline files
        if updated_description != doc.get("description"):
            session.put(
                f"https://ticket.nextoraerp.com/api/resource/Issue/{remote_doc_name}",
                json={"description": updated_description}
            )

        # Step 8: Save remote ticket ID back to local issue
        frappe.db.set_value("Issue", doc.name, "ticket_portal_id", remote_doc_name)
        frappe.db.commit()

        frappe.msgprint(
            f"Issue synced successfully.<br>"
            f"Ticket ID: <b>{remote_doc_name}</b><br>"
            f"Site: <b>{frappe.local.site}</b><br>"
            f"Attachments: <b>{len(uploaded_files)}</b>",
            title="Ticket Created",
            indicator="green"
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Ticket Sync Exception")

# ─────────────────────────────────────────────
# REVERSE SYNC: ticket.nextoraerp.com → source site
# Only runs when custom_by_ticket = 1
# ─────────────────────────────────────────────
def sync_to_ticket_portal(doc):
    try:
        session = requests.Session()

        # STEP 1: LOGIN
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

        login_data = login_res.json()

        frappe.log_error(
            str(login_data),
            "Login Response"
        )

        if login_data.get("message") != "Logged In":
            frappe.log_error(
                str(login_data),
                "Login Failed"
            )

            frappe.db.set_value(
                "Issue",
                doc.name,
                "ticket_sync_status",
                "Failed"
            )

            return

        # STEP 2: VALID EMAIL
        raised_by = doc.get("raised_by")

        if not raised_by or "@" not in str(raised_by):

            raised_by = frappe.db.get_value(
                "User",
                frappe.session.user,
                "email"
            )

        # STEP 3: PAYLOAD
        payload = {
            "subject":
            doc.get("subject"),

            "raised_by":
            raised_by,

            "description":
            doc.get("description")
            or "",

            "resolution_details":
            doc.get(
                "resolution_details"
            ),

            "via_customer_portal":
            doc.get(
                "via_customer_portal"
            ),

            "custom_site_name":
            frappe.local.site,

            "issue_type":
            doc.get(
                "issue_type"
            ),

            "priority":
            doc.get(
                "priority"
            ),

            "custom_issue_id":
            doc.name
        }

        payload = {
            k: v
            for k, v in payload.items()
            if v not in [None, ""]
        }

        frappe.log_error(
            str(payload),
            "Payload"
        )

        # STEP 4: CREATE ISSUE
        response = session.post(
            "https://ticket.nextoraerp.com/api/resource/Issue",
            json=payload
        )

        result = response.json()

        frappe.log_error(
            str(result),
            "Create Response"
        )

        if not result.get("data"):

            frappe.db.set_value(
                "Issue",
                doc.name,
                "ticket_sync_status",
                "Failed"
            )

            frappe.log_error(
                str(result),
                "Create Failed"
            )

            return

        remote_doc_name = (
            result["data"]
            .get("name")
        )

        # STEP 5: GET ATTACHMENTS
        attachments = frappe.get_all(
            "File",
            filters={
                "attached_to_doctype":
                "Issue",

                "attached_to_name":
                doc.name
            },
            fields=[
                "file_name",
                "file_url",
                "is_private"
            ]
        )

        frappe.log_error(
            str(attachments),
            "Attachments"
        )

        uploaded_files = []

        # STEP 6: UPLOAD FILES
        for att in attachments:

            try:
                file_url = att.get(
                    "file_url"
                )

                if not file_url:
                    continue

                # private file
                if file_url.startswith(
                    "/private"
                ):

                    file_path = (
                        frappe.get_site_path()
                        + file_url
                    )

                # public file
                elif file_url.startswith(
                    "/files"
                ):

                    file_path = (
                        frappe.get_site_path(
                            "public"
                        )
                        + file_url
                    )

                else:
                    continue

                with open(
                    file_path,
                    "rb"
                ) as f:

                    file_content = (
                        f.read()
                    )

                upload_res = (
                    session.post(
                        "https://ticket.nextoraerp.com/api/method/upload_file",
                        data={
                            "doctype":
                            "Issue",

                            "docname":
                            remote_doc_name,

                            "fieldname":
                            "attachment",

                            "is_private":
                            att.get(
                                "is_private",
                                0
                            )
                        },
                        files={
                            "file":
                            (
                                att.get(
                                    "file_name"
                                ),
                                file_content
                            )
                        }
                    )
                )

                upload_result = (
                    upload_res.json()
                )

                frappe.log_error(
                    str(upload_result),
                    "Attachment Upload"
                )

                if upload_result.get(
                    "message"
                ):
                    uploaded_files.append(
                        att.get(
                            "file_name"
                        )
                    )

            except Exception as e:

                frappe.log_error(
                    str(e),
                    "Attachment Error"
                )

        # STEP 7: UPDATE STATUS
        frappe.db.set_value(
            "Issue",
            doc.name,
            {
                "ticket_portal_id":
                remote_doc_name,

                "ticket_sync_status":
                "Synced"
            }
        )

        frappe.db.commit()

        frappe.log_error(
            f"Synced to ticket portal "
            f"({remote_doc_name})",
            "Sync Success"
        )

        # STEP 8: SUCCESS POPUP
        frappe.msgprint(
            f"""
            Issue synced successfully.<br><br>

            Ticket ID:
            <b>{remote_doc_name}</b><br>

            Site:
            <b>{frappe.local.site}</b><br>

            Attachments uploaded:
            <b>{len(uploaded_files)}</b>
            """,
            title="Ticket Created",
            indicator="green"
        )

    except Exception as e:

        frappe.db.set_value(
            "Issue",
            doc.name,
            "ticket_sync_status",
            "Failed"
        )

        frappe.log_error(
            str(e),
            "Ticket Sync Exception"
        )