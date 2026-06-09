frappe.provide("supportnextro");

function add_raise_issue_btn() {
    // Hide button on Issue doctype pages
    if (frappe.get_route()[0] === "Form" && frappe.get_route()[1] === "Issue") return;
    if (frappe.get_route()[0] === "List" && frappe.get_route()[1] === "Issue") return;

    const targets = new Set([
        ".page-head-content .page-actions"
    ]);

    targets.forEach(selector => {
        const container = $(selector);
        if (!container.length) return;
        if (container.find("#raise-issue-btn").length) return;

        container.prepend(`
            <button id="raise-issue-btn" class="btn btn-danger btn-sm" style="margin-right:8px;">
                <i class="fa fa-flag"></i> Report Issue
            </button>
        `);
    });

    $(document).off("click", "#raise-issue-btn").on("click", "#raise-issue-btn", function () {
        frappe.new_doc("Issue");
    });
}

$(document).on("page-change", () => {
    // Remove button immediately on page change to Issue
    if (frappe.get_route()[0] === "Form" && frappe.get_route()[1] === "Issue") {
        $("#raise-issue-btn").remove();
        return;
    }
    if (frappe.get_route()[0] === "List" && frappe.get_route()[1] === "Issue") {
        $("#raise-issue-btn").remove();
        return;
    }
    setTimeout(add_raise_issue_btn, 300);
});

$(document).ready(() => setTimeout(add_raise_issue_btn, 500));