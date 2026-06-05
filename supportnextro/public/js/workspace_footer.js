frappe.provide("supportnextro");

function add_raise_issue_btn() {
    const targets = new Set([
        ".page-head-content .page-actions"  // top right where mouse pointer is
        
    ]);

    targets.forEach(selector => {
        const container = $(selector);
        if (!container.length) return;
        if (container.find("#raise-issue-btn").length) return;

        container.prepend(`
            <button id="raise-issue-btn" class="btn btn-danger btn-sm" style="margin-right:8px;">
                <i class="fa fa-flag"></i> Raise Issue
            </button>
        `);
    });

    $(document).off("click", "#raise-issue-btn").on("click", "#raise-issue-btn", function () {
        frappe.new_doc("Issue");
    });
}

$(document).on("page-change", () => setTimeout(add_raise_issue_btn, 300));
$(document).ready(() => setTimeout(add_raise_issue_btn, 500));