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
#     source_site = doc.get("custom_site_name")
#     if not source_site:
#         frappe.log_error("No source site found", "Reverse Sync Skipped")
#         return

#     try:
#         # Use API key/secret instead of session login
#         # Store these in Frappe's "Website Settings" or a custom doctype / environment
#         api_key = frappe.conf.get("reverse_sync_api_key")
#         api_secret = frappe.conf.get("reverse_sync_api_secret")

#         if not api_key or not api_secret:
#             frappe.log_error("API credentials not configured", "Reverse Sync Skipped")
#             return

#         headers = {
#             "Authorization": f"token {api_key}:{api_secret}",
#             "Content-Type": "application/json"
#         }

#         base_url = f"https://{source_site}"

#         # Step 1: Find issue by custom_issue_id on source site
#         custom_issue_id = doc.get("custom_issue_id")

#         if custom_issue_id:
#             search_res = requests.get(
#                 f"{base_url}/api/resource/Issue/{custom_issue_id}",
#                 headers=headers
#             )
#             search_res.raise_for_status()
#             issue_data = search_res.json().get("data")
#             local_issue_name = issue_data.get("name") if issue_data else None
#         else:
#             search_res = requests.get(
#                 f"{base_url}/api/resource/Issue",
#                 headers=headers,
#                 params={
#                     "filters": f'[["ticket_portal_id","=","{doc.name}"]]',
#                     "fields": '["name"]',
#                     "limit": 1
#                 }
#             )
#             search_res.raise_for_status()
#             issues = search_res.json().get("data", [])
#             local_issue_name = issues[0]["name"] if issues else None

#         frappe.log_error(str(local_issue_name), "Reverse Sync Local Issue")

#         if not local_issue_name:
#             frappe.log_error(
#                 f"No issue found on {source_site} for ticket {doc.name}",
#                 "Reverse Sync Not Found"
#             )
#             return

#         # Step 2: Build payload
#         payload = {
#             k: v for k, v in {
#                 "status": doc.get("status"),
#                 "description": doc.get("description"),
#             }.items() if v is not None and v != ""
#         }

#         frappe.log_error(str(payload), "Reverse Sync Payload")

#         # Step 3: Update issue on source site
#         update_res = requests.put(
#             f"{base_url}/api/resource/Issue/{local_issue_name}",
#             headers=headers,
#             json=payload
#         )
#         update_res.raise_for_status()

#         result = update_res.json()
#         frappe.log_error(str(result), "Reverse Sync Update Response")

#         if result.get("data"):
#             frappe.log_error(
#                 f"Reverse synced → {source_site} : {local_issue_name}",
#                 "Reverse Sync Success"
#             )
#         else:
#             frappe.log_error(str(result), "Reverse Sync Failed")

#     except Exception:
#         frappe.log_error(frappe.get_traceback(), "Reverse Sync Exception")


































import frappe
import requests
import base64
import re


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def safe_log(title, message):
    """
    Prevent Error Log title overflow
    """
    try:
        frappe.log_error(
            message=str(message)[:5000],
            title=str(title)[:140]
        )
    except Exception:
        pass


def after_insert(doc, method):
    pass


def on_update(doc, method):

    # Prevent recursive save loop
    if frappe.flags.in_ticket_sync:
        return

    frappe.flags.in_ticket_sync = True

    try:
        # Reverse sync
        if doc.get("custom_by_ticket") == 1 and doc.get("custom_site_name"):
            reverse_sync_to_source(doc)

        # Forward sync
        else:
            sync_to_ticket_portal(doc)

    finally:
        frappe.flags.in_ticket_sync = False


def get_valid_email(raised_by):
    if raised_by and "@" in raised_by:
        return raised_by

    email = frappe.db.get_value(
        "User",
        frappe.session.user,
        "email"
    )

    return email if email and "@" in email else None


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

        return (
            file_url.split("/")[-1],
            base64.b64encode(file_data).decode("utf-8")
        )

    except Exception:
        safe_log(
            "File Read Error",
            frappe.get_traceback()
        )
        return None, None


def upload_file_to_portal(
    session,
    file_name,
    file_content,
    doctype,
    docname
):
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
                "file": (file_name, file_content)
            }
        )

        result = response.json()

        if result.get("message"):
            return result["message"].get("file_url")

        safe_log(
            "Upload File Error",
            str(result)
        )

    except Exception:
        safe_log(
            "File Upload Error",
            frappe.get_traceback()
        )

    return None


def fix_description_links(
    description,
    session,
    remote_doc_name
):
    if not description:
        return description

    pattern = r'(src|href)=["\']((\/private\/files\/|\/files\/)[^"\']+)["\']'
    matches = re.findall(pattern, description)

    for _, file_url, _ in matches:

        file_name, file_base64 = get_file_base64(file_url)

        if not file_name:
            continue

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


# ---------------------------------------------------------
# FORWARD SYNC
# demo → ticket
# ---------------------------------------------------------

def sync_to_ticket_portal(doc):

    try:
        session = requests.Session()

        login_res = session.post(
            "https://ticket.nextoraerp.com/api/method/login",
            data={
                "usr": "ticketuser@gmaill.com",
                "pwd": "ticketuser@123"
            }
        )

        if login_res.json().get("message") != "Logged In":
            safe_log(
                "Ticket Login Failed",
                login_res.text
            )
            return

        # Update existing ticket
        if doc.get("ticket_portal_id"):

            payload = {
                "status": doc.status,
                "description": doc.description
            }

            res = session.put(
                f"https://ticket.nextoraerp.com/api/resource/Issue/{doc.ticket_portal_id}",
                json=payload
            )

            safe_log(
                "Forward Sync Update",
                res.text
            )

            return

        # CREATE NEW ISSUE
        payload = {
            "subject": doc.subject,
            "raised_by": get_valid_email(
                doc.get("raised_by")
            ),
            "description": doc.description or "",
            "priority": doc.priority,
            "issue_type": doc.issue_type,
            "custom_issue_id": doc.name,
            "custom_site_name": frappe.local.site,
            "custom_by_ticket": 1,
        }

        response = session.post(
            "https://ticket.nextoraerp.com/api/resource/Issue",
            json=payload
        )

        result = response.json()

        if not result.get("data"):
            safe_log(
                "Ticket Create Failed",
                result
            )
            return

        remote_doc_name = result["data"]["name"]

        frappe.db.set_value(
            "Issue",
            doc.name,
            "ticket_portal_id",
            remote_doc_name
        )

        attachments = frappe.get_all(
            "File",
            filters={
                "attached_to_doctype": "Issue",
                "attached_to_name": doc.name
            },
            fields=["file_url"]
        )

        for att in attachments:

            file_name, file_base64 = get_file_base64(
                att.file_url
            )

            if not file_name:
                continue

            upload_file_to_portal(
                session,
                file_name,
                base64.b64decode(file_base64),
                "Issue",
                remote_doc_name
            )

    except Exception:
        safe_log(
            "Forward Sync Exception",
            frappe.get_traceback()
        )


# ---------------------------------------------------------
# REVERSE SYNC
# ticket → source
# ---------------------------------------------------------

def reverse_sync_to_source(doc):

    try:
        source_site = doc.get("custom_site_name")

        if not source_site:
            return

        api_key = frappe.conf.get(
            "reverse_sync_api_key"
        )

        api_secret = frappe.conf.get(
            "reverse_sync_api_secret"
        )

        if not api_key or not api_secret:
            safe_log(
                "Reverse Sync",
                "API credentials missing"
            )
            return

        headers = {
            "Authorization":
            f"token {api_key}:{api_secret}"
        }

        issue_name = (
            doc.get("custom_issue_id")
            or doc.get("ticket_portal_id")
        )

        if not issue_name:
            safe_log(
                "Reverse Sync",
                "Issue name missing"
            )
            return

        session = requests.Session()
        session.headers.update(headers)

        # ------------------------------------------------
        # STEP 1: Upload attachments to source site
        # ------------------------------------------------

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

            file_name, file_base64 = get_file_base64(
                att.file_url
            )

            if not file_name or not file_base64:
                continue

            try:
                file_content = base64.b64decode(
                    file_base64
                )

                response = session.post(
                    f"https://{source_site}/api/method/upload_file",
                    data={
                        "doctype": "Issue",
                        "docname": issue_name,
                        "fieldname": "attachment",
                        "is_private": 0
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
                    uploaded_url = result[
                        "message"
                    ].get("file_url")

                    uploaded_files_map[
                        att.file_url
                    ] = uploaded_url

            except Exception:
                safe_log(
                    "Reverse File Upload",
                    frappe.get_traceback()
                )

        # ------------------------------------------------
        # STEP 2: Fix inline file URLs in description
        # ------------------------------------------------

        description = (
            doc.get("description") or ""
        )

        for old_url, new_url in (
            uploaded_files_map.items()
        ):
            description = description.replace(
                old_url,
                f"https://{source_site}{new_url}"
            )

        # ------------------------------------------------
        # STEP 3: Update source issue
        # ------------------------------------------------

        payload = {
            "status": doc.get("status"),
            "description": description
        }

        res = session.put(
            f"https://{source_site}/api/resource/Issue/{issue_name}",
            json=payload,
            timeout=20
        )

        safe_log(
            "Reverse Sync Success",
            res.text
        )

    except Exception:
        safe_log(
            "Reverse Sync Exception",
            frappe.get_traceback()
        )