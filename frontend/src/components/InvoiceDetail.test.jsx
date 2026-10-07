import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { API_URL } from "../api.js";
import heldUncertain from "../test/fixtures/detail_held_uncertain.json";
import posted from "../test/fixtures/detail_posted.json";
import InvoiceDetail from "./InvoiceDetail.jsx";

function stubFetch(responseBody) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(responseBody), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderDetail(detail, onAction = vi.fn()) {
  render(<InvoiceDetail detail={detail} onAction={onAction} setError={vi.fn()} />);
  return onAction;
}

function qcPanel() {
  return screen.getByRole("heading", { name: "QC actions" }).closest(".panel");
}

describe("InvoiceDetail QC actions", () => {
  it("escalate POSTs to the API and hands the refreshed detail to onAction", async () => {
    const fetchMock = stubFetch({ ...heldUncertain, invoice: { ...heldUncertain.invoice } });
    const onAction = renderDetail(heldUncertain);
    const id = heldUncertain.invoice.id;

    await userEvent.click(within(qcPanel()).getByRole("button", { name: "Escalate" }));

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_URL}/api/invoices/${id}/escalate`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ reason: null });
    await vi.waitFor(() => expect(onAction).toHaveBeenCalledOnce());
  });

  it("only renders the actions the item's status allows", () => {
    renderDetail(posted);
    const panel = qcPanel();
    expect(posted.allowed_actions).toEqual([]);
    for (const name of ["Rerun with corrections", "Escalate", "Reject (not a receipt)"]) {
      expect(within(panel).queryByRole("button", { name })).toBeNull();
    }
    expect(screen.getByRole("button", { name: "Save metadata corrections" })).toBeDisabled();
  });

  it("held items offer rerun, escalate, and reject", () => {
    renderDetail(heldUncertain);
    const panel = qcPanel();
    for (const name of ["Rerun with corrections", "Escalate", "Reject (not a receipt)"]) {
      expect(within(panel).getByRole("button", { name })).toBeInTheDocument();
    }
  });
});

describe("InvoiceDetail review gate", () => {
  const uncertain = heldUncertain.citations.filter((c) => c.status === "uncertain");

  it("blocks 'Mark reviewed' until every uncertain field is confirmed", async () => {
    expect(uncertain.length).toBeGreaterThan(0);
    const fetchMock = stubFetch(heldUncertain);
    renderDetail(heldUncertain);

    expect(within(qcPanel()).getByRole("button", { name: "Mark reviewed" })).toBeDisabled();

    const verify = screen.getByRole("heading", { name: /Uncertain fields to verify/ }).closest(".panel");
    await userEvent.click(within(verify).getAllByRole("button", { name: "Confirm" })[0]);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_URL}/api/invoices/${heldUncertain.invoice.id}/citations/confirm`);
    expect(JSON.parse(init.body).target_id).toBe(uncertain[0].target_id);
  });

  it("unblocks 'Mark reviewed' once the confirmations are on the audit trail", () => {
    const confirmed = uncertain.map((c, i) => ({
      id: `evt_confirm_${i}`,
      invoice_id: heldUncertain.invoice.id,
      actor: "human",
      action: "confirmed",
      details: { target_id: c.target_id },
      timestamp: "2026-10-06T00:00:00Z",
    }));
    renderDetail({ ...heldUncertain, audit: [...heldUncertain.audit, ...confirmed] });

    expect(within(qcPanel()).getByRole("button", { name: "Mark reviewed" })).toBeEnabled();
  });
});
