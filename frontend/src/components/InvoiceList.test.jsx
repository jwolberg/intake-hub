import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import rows from "../test/fixtures/list.json";
import InvoiceList from "./InvoiceList.jsx";

function bodyRows() {
  const [, tbody] = screen.getAllByRole("rowgroup");
  return within(tbody).getAllByRole("row");
}

describe("InvoiceList filter chips", () => {
  it("counts each triage tag and filters rows to the chosen chip", async () => {
    render(<InvoiceList invoices={rows} total={rows.length} onSelect={() => {}} />);
    const held = rows.filter((r) => r.filter_tags.includes("held"));
    expect(held.length).toBeGreaterThan(0);
    expect(held.length).toBeLessThan(rows.length);

    expect(bodyRows()).toHaveLength(rows.length);
    const heldChip = screen.getByRole("button", { name: /^Held/ });
    expect(heldChip).toHaveTextContent(String(held.length));

    await userEvent.click(heldChip);
    expect(bodyRows()).toHaveLength(held.length);
    for (const row of bodyRows()) expect(row).toHaveTextContent("held");

    await userEvent.click(heldChip); // toggles back to all
    expect(bodyRows()).toHaveLength(rows.length);
  });

  it("selects an invoice when its row is clicked", async () => {
    const onSelect = vi.fn();
    render(<InvoiceList invoices={rows} total={rows.length} onSelect={onSelect} />);
    await userEvent.click(bodyRows()[0]);
    expect(onSelect).toHaveBeenCalledWith(rows[0].id);
  });

  it("offers Load more only when the server has more rows", async () => {
    const onLoadMore = vi.fn();
    const { rerender } = render(
      <InvoiceList invoices={rows} total={rows.length} onLoadMore={onLoadMore} onSelect={() => {}} />,
    );
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();

    rerender(
      <InvoiceList invoices={rows} total={rows.length + 5} onLoadMore={onLoadMore} onSelect={() => {}} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(onLoadMore).toHaveBeenCalledOnce();
  });
});
