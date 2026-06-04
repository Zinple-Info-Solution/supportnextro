# import frappe
# import requests
# import base64
# import re

# def after_insert(doc, method):
#     pass

# def on_update(doc, method):
#     if getattr(doc, "_syncing", False):
#         return

#     doc._syncing = True

#     # If custom_by_ticket = 1 and custom_site_name exists
#     # this doc is on ticket.nextoraerp.com — sync back to source site
#     if doc.get("custom_by_ticket") == 1 and doc.get("custom_site_name"):
#         reverse_sync_to_source(doc)
#     else:
#         # Normal forward sync — your site → ticket.nextoraerp.com
#         sync_to_ticket_portal(doc)

# def get_valid_email(raised_by):
#     if raised_by and "@" in raised_by:
#         return raised_by
#     email = frappe.db.get_value("User", frappe.session.user, "email")
#     if email and "@" in email:
#         return email
#     return None

# def get_file_base64(file_url):
#     try:
#         if file_url.startswith("/private"):
#             file_path = frappe.get_site_path() + file_url
#         elif file_url.startswith("/files"):
#             file_path = frappe.get_site_path("public") + file_url
#         else:
#             return None, None

#         with open(file_path, "rb") as f:
#             file_data = f.read()

#         file_name = file_url.split("/")[-1]
#         encoded   = base64.b64encode(file_data).decode("utf-8")
#         return file_name, encoded

#     except Exception:
#         frappe.log_error(frappe.get_traceback(), "File Read Error")
#         return None, None

# def upload_file_to_portal(session, file_name, file_content, doctype, docname):
#     try:
#         response = session.post(
#             "https://ticket.nextoraerp.com/api/method/upload_file",
#             data={
#                 "is_private": 0,
#                 "doctype"   : doctype,
#                 "docname"   : docname,
#                 "fieldname" : "attachment",
#             },
#             files={"file": (file_name, file_content)}
#         )
#         result = response.json()
#         if result.get("message"):
#             return result["message"].get("file_url")
#         frappe.log_error(str(result), "File Upload Response Error")
#         return None
#     except Exception:
#         frappe.log_error(frappe.get_traceback(), "File Upload Error")
#         return None

# def fix_description_links(description, session, remote_doc_name):
#     if not description:
#         return description

#     pattern = r'(src|href)=["\']((\/private\/files\/|\/files\/)[^"\']+)["\']'
#     matches  = re.findall(pattern, description)

#     for attr, file_url, _ in matches:
#         file_name, file_base64 = get_file_base64(file_url)
#         if file_name and file_base64:
#             file_content = base64.b64decode(file_base64)
#             remote_url   = upload_file_to_portal(
#                 session, file_name, file_content, "Issue", remote_doc_name
#             )
#             if remote_url:
#                 description = description.replace(
#                     file_url,
#                     f"https://ticket.nextoraerp.com{remote_url}"
#                 )
#     return description

# # ─────────────────────────────────────────────
# # FORWARD SYNC: your site → ticket.nextoraerp.com
# # ─────────────────────────────────────────────
# def sync_to_ticket_portal(doc):
#     try:
#         # Step 1: Login
#         session = requests.Session()
#         login_res = session.post(
#             "https://ticket.nextoraerp.com/api/method/login",
#             data={
#                 "usr": "ticketuser@gmaill.com",
#                 "pwd": "ticketuser@123"
#             },
#             headers={"Content-Type": "application/x-www-form-urlencoded"}
#         )

#         if login_res.json().get("message") != "Logged In":
#             frappe.log_error("Login failed", "Ticket Sync")
#             return

#         # Step 2: Fetch attachments
#         attachments = frappe.get_all(
#             "File",
#             filters={
#                 "attached_to_doctype": "Issue",
#                 "attached_to_name"   : doc.name
#             },
#             fields=["file_name", "file_url", "is_private"]
#         )

#         frappe.log_error(
#             f"Found {len(attachments)} attachments for {doc.name}",
#             "Attachment Debug"
#         )

#         # Step 3: Build payload
#         payload = {
#             "subject"            : doc.get("subject"),
#             "raised_by"          : get_valid_email(doc.get("raised_by")),
#             "description"        : doc.get("description") or "",
#             "resolution_details" : doc.get("resolution_details"),
#             "via_customer_portal": doc.get("via_customer_portal"),
#             "custom_site_name"   : frappe.local.site,
#             "issue_type"         : doc.get("issue_type"),
#             "priority"           : doc.get("priority"),
#             "custom_issue_id"    : doc.get("name"),
#             "custom_by_ticket"   : 1,
#         }

#         payload = {k: v for k, v in payload.items() if v is not None and v != ""}

#         # Step 4: Create issue on remote
#         response = session.post(
#             "https://ticket.nextoraerp.com/api/resource/Issue",
#             json=payload
#         )

#         result = response.json()

#         if not result.get("data"):
#             frappe.log_error(str(result), "Ticket Sync Failed")
#             return

#         remote_doc_name = result["data"].get("name")
#         frappe.log_error(f"Remote issue created: {remote_doc_name}", "Ticket Sync")

#         # Step 5: Upload attachments
#         uploaded_files = []
#         for att in attachments:
#             file_name, file_base64 = get_file_base64(att.file_url)
#             if file_name and file_base64:
#                 file_content = base64.b64decode(file_base64)
#                 remote_url   = upload_file_to_portal(
#                     session, file_name, file_content, "Issue", remote_doc_name
#                 )
#                 if remote_url:
#                     uploaded_files.append(file_name)

#         # Step 6: Fix inline images in description
#         updated_description = fix_description_links(
#             doc.get("description"), session, remote_doc_name
#         )

#         # Step 7: Update remote if description had inline files
#         if updated_description != doc.get("description"):
#             session.put(
#                 f"https://ticket.nextoraerp.com/api/resource/Issue/{remote_doc_name}",
#                 json={"description": updated_description}
#             )

#         # Step 8: Save remote ticket ID back to local issue
#         frappe.db.set_value("Issue", doc.name, "ticket_portal_id", remote_doc_name)
#         frappe.db.commit()

#         frappe.msgprint(
#             f"Issue synced successfully.<br>"
#             f"Ticket ID: <b>{remote_doc_name}</b><br>"
#             f"Site: <b>{frappe.local.site}</b><br>"
#             f"Attachments: <b>{len(uploaded_files)}</b>",
#             title="Ticket Created",
#             indicator="green"
#         )

#     except Exception:
#         frappe.log_error(frappe.get_traceback(), "Ticket Sync Exception")

# # ─────────────────────────────────────────────
# # REVERSE SYNC: ticket.nextoraerp.com → source site
# # Only runs when custom_by_ticket = 1
# # ─────────────────────────────────────────────

# def reverse_sync_to_source(doc):
#     """
#     Sync status/description changes from ticket site → source site.
#     """

#     # ── Skip if this save was triggered by a sync from source site ──
#     if doc.get("custom_is_syncing"):
#         frappe.db.set_value("Issue", doc.name, "custom_is_syncing", 0)
#         return

#     # ── Only run for tickets that were forward-synced ──
#     source_site = doc.get("custom_site_name")
#     custom_issue_id = doc.get("custom_issue_id")

#     if not source_site or not custom_issue_id:
#         return

#     # ── Only sync if relevant fields actually changed ──
#     if not (doc.has_value_changed("status") or doc.has_value_changed("description")):
#         return

#     try:
#         http = requests.Session()

#         # Step 1: Login
#         login_res = http.post(
#             f"https://{source_site}/api/method/login",
#             data={"usr": "ticket_support", "pwd": "support@zinple"},
#             headers={"Content-Type": "application/x-www-form-urlencoded"},
#             timeout=10
#         )

#         if login_res.json().get("message") != "Logged In":
#             frappe.log_error(
#                 f"Login failed: {source_site}",
#                 "Reverse Sync Login Failed"
#             )
#             return

#         # Step 2: Build payload
#         payload = {}
#         if doc.has_value_changed("status"):
#             payload["status"] = doc.status
#         if doc.has_value_changed("description"):
#             payload["description"] = doc.description

#         # ✅ Tell source site: skip your sync hooks for this update
#         payload["custom_is_syncing"] = 1

#         # Step 3: Update issue on source site
#         update_res = http.put(
#             f"https://{source_site}/api/resource/Issue/{custom_issue_id}",
#             json=payload,
#             timeout=10
#         )

#         result = update_res.json()

#         if not result.get("data"):
#             frappe.log_error(
#                 f"Update failed for {custom_issue_id}: {str(result)[:120]}",
#                 "Reverse Sync Failed"
#             )

#     except Exception:
#         frappe.log_error(frappe.get_traceback(), "Reverse Sync Exception")


























import frappe
import requests
import base64
import re


# def safe_log(title, message):
#     """
#     Safe log for large payloads / files
#     """
#     try:
#         frappe.log_error(
#             str(message)[:5000],   # message
#             str(title)[:140]       # title
#         )
#     except Exception:
#         pass

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

        # frappe.log_error(
        #     f"Found {len(attachments)} attachments for {doc.name}",
        #     "Attachment Debug"
        # )
        safe_log(
    "Attachment Debug",
    f"Found {len(attachments)} attachments for {doc.name}"
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
            safe_log(
    "Ticket Sync Failed",
    result
)
            return

        remote_doc_name = result["data"].get("name")
        safe_log(
    "Ticket Sync",
    f"Remote issue created: {remote_doc_name}"
)
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

        # frappe.msgprint(
        #     f"Issue synced successfully.<br>"
        #     f"Ticket ID: <b>{remote_doc_name}</b><br>"
        #     f"Site: <b>{frappe.local.site}</b><br>"
        #     f"Attachments: <b>{len(uploaded_files)}</b>",
        #     title="Ticket Created",
        #     indicator="green"
        # )
        safe_log(
    "Ticket Sync Success",
    f"""
    Ticket ID: {remote_doc_name}
    Site: {frappe.local.site}
    Attachments: {len(uploaded_files)}
    """
)

    # except Exception:
        # frappe.log_error(frappe.get_traceback(), "Ticket Sync Exception")

    except Exception:
        safe_log(
            "Ticket Sync Exception",
            frappe.get_traceback()
        )

# ─────────────────────────────────────────────
# REVERSE SYNC: ticket.nextoraerp.com → source site
# Only runs when custom_by_ticket = 1
# ─────────────────────────────────────────────

# def reverse_sync_to_source(doc):
#     """
#     Sync status/description changes from ticket site → source site.
#     """

#     # ── Skip if this save was triggered by a sync from source site ──
#     if doc.get("custom_is_syncing"):
#         frappe.db.set_value("Issue", doc.name, "custom_is_syncing", 0)
#         return

#     # ── Only run for tickets that were forward-synced ──
#     source_site = doc.get("custom_site_name")
#     custom_issue_id = doc.get("custom_issue_id")

#     if not source_site or not custom_issue_id:
#         return

#     # ── Only sync if relevant fields actually changed ──
#     if not (doc.has_value_changed("status") or doc.has_value_changed("description")):
#         return

#     try:
#         http = requests.Session()

#         # Step 1: Login
#         login_res = http.post(
#             f"https://{source_site}/api/method/login",
#             data={"usr": "ticket_support", "pwd": "support@zinple"},
#             headers={"Content-Type": "application/x-www-form-urlencoded"},
#             timeout=120
#         )

#         if login_res.json().get("message") != "Logged In":
#             # frappe.log_error(
#             #     f"Login failed: {source_site}",
#             #     "Reverse Sync Login Failed"
#             # )
#             safe_log(
#     "Reverse Sync Login Failed",
#     f"Login failed: {source_site}"
# )
#             return

#         # Step 2: Build payload
#         payload = {}
#         if doc.has_value_changed("status"):
#             payload["status"] = doc.status
#         if doc.has_value_changed("description"):
#             payload["description"] = doc.description

#         # ✅ Tell source site: skip your sync hooks for this update
#         payload["custom_is_syncing"] = 1

#         # Step 3: Update issue on source site
#         update_res = http.put(
#             f"https://{source_site}/api/resource/Issue/{custom_issue_id}",
#             json=payload,
#             timeout=120
#         )

#         result = update_res.json()

#         if not result.get("data"):
#             # frappe.log_error(
#             #     f"Update failed for {custom_issue_id}: {str(result)[:120]}",
#             #     "Reverse Sync Failed"
#             # )
#             safe_log(
#     "Reverse Sync Failed",
#     result
# )

#     except Exception:
#         # frappe.log_error(frappe.get_traceback(), "Reverse Sync Exception")
#         safe_log(
#     "Ticket Sync Exception",
#     frappe.get_traceback()
# )



def safe_log(title, message):
    try:
        frappe.log_error(
            str(message)[:5000],
            str(title)[:140]
        )
    except Exception:
        pass


def reverse_sync_to_source(doc):

    try:
        # ---------------------------------------
        # Prevent loop sync
        # ---------------------------------------
        if doc.get("custom_is_syncing"):
            frappe.db.set_value(
                "Issue",
                doc.name,
                "custom_is_syncing",
                0
            )
            frappe.db.commit()
            return

        source_site = doc.get("custom_site_name")
        custom_issue_id = doc.get("custom_issue_id")

        if not source_site:
            safe_log(
                "Reverse Sync",
                "Source site missing"
            )
            return

        if not custom_issue_id:
            safe_log(
                "Reverse Sync",
                "Issue ID missing"
            )
            return

        # ---------------------------------------
        # Sync only changed fields
        # ---------------------------------------
        changed = (
            doc.has_value_changed("status")
            or doc.has_value_changed("description")
        )

        if not changed:
            return

        # ---------------------------------------
        # Login
        # ---------------------------------------
        session = requests.Session()

        login_res = session.post(
            f"https://{source_site}/api/method/login",
            data={
                "usr": "ticket_support",
                "pwd": "support@zinple"
            },
            headers={
                "Content-Type":
                "application/x-www-form-urlencoded"
            },
            timeout=120
        )

        login_json = login_res.json()

        if login_json.get("message") != "Logged In":
            safe_log(
                "Reverse Login Failed",
                str(login_json)
            )
            return

        # ---------------------------------------
        # Get CSRF token after login
        # ---------------------------------------
        csrf_token = session.cookies.get("csrf_token", "")

        # ---------------------------------------
        # Upload attachments
        # ---------------------------------------
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

        uploaded_files_map = {}

        for att in attachments:

            try:
                file_name, file_base64 = get_file_base64(
                    att.file_url
                )

                if not file_name or not file_base64:
                    continue

                file_content = base64.b64decode(
                    file_base64
                )

                upload_res = session.post(
                    f"https://{source_site}/api/method/upload_file",
                    data={
                        "doctype": "Issue",
                        "docname": custom_issue_id,
                        "fieldname": "attachment",
                        "is_private": 0
                    },
                    files={
                        "file": (
                            file_name,
                            file_content
                        )
                    },
                    headers={
                        "X-Frappe-CSRF-Token": csrf_token
                    },
                    timeout=300
                )

                try:
                    result = upload_res.json()
                except Exception:
                    result = {}

                if result.get("message"):
                    uploaded_url = result["message"].get("file_url")

                    if uploaded_url:
                        uploaded_files_map[att.file_url] = uploaded_url

            except Exception:
                safe_log(
                    "Reverse File Upload Error",
                    frappe.get_traceback()
                )

        # ---------------------------------------
        # Fix description image URLs
        # ---------------------------------------
        description = doc.description or ""

        for old_url, new_url in uploaded_files_map.items():
            description = description.replace(
                old_url,
                f"https://{source_site}{new_url}"
            )

        # Convert private ticket URLs
        description = description.replace(
            "https://ticket.nextoraerp.com/private/files/",
            f"https://{source_site}/files/"
        )

        # ---------------------------------------
        # Payload
        # ---------------------------------------
        payload = {
            "custom_is_syncing": 1
        }

        if doc.has_value_changed("status"):
            payload["status"] = doc.status

        if doc.has_value_changed("description"):
            payload["description"] = description

        # ---------------------------------------
        # Update issue on source site
        # ---------------------------------------
        update_res = session.put(
            f"https://{source_site}/api/resource/Issue/{custom_issue_id}",
            json=payload,
            headers={
                "X-Frappe-CSRF-Token": csrf_token
            },
            timeout=300
        )

        try:
            result = update_res.json()
        except Exception:
            result = {
                "response": update_res.text[:1000]
            }

        if not result.get("data"):
            safe_log(
                "Reverse Sync Failed",
                str(result)[:5000]
            )
        else:
            safe_log(
                "Reverse Sync Success",
                f"{custom_issue_id} synced successfully"
            )

    except Exception:
        safe_log(
            "Reverse Sync Exception",
            frappe.get_traceback()
        )