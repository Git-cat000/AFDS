import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProjectBudget } from "../src/ProjectBudget";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("requires billing confirmation and submits a zero actual charge with project revision", async () => {
  const data = {
    revision: 8,
    budget_limit: "10.000000",
    currency: "USD",
    spent: "0.000000",
    reserved: "0.500000",
    unknown_count: 0,
    allow_remote: true,
    allow_unknown_cost: false,
    over_budget: false,
    entries: [
      {
        id: "charge-1",
        reference: "job:123",
        provider_id: "openai-video",
        state: "pending",
        estimate: "0.500000",
        actual: null,
        currency: "USD",
      },
    ],
  };
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(
      async () => new Response(JSON.stringify(data), { status: 200 }),
    );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ProjectBudget
        projectId="project-1"
        busy={false}
        run={async (action) => {
          await action();
        }}
      />
    </QueryClientProvider>,
  );
  const button = await screen.findByRole("button", { name: "确认实际费用" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("实际费用 charge-1"), {
    target: { value: "0" },
  });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByLabelText("已根据服务商账单核对"));
  fireEvent.click(button);
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(
      true,
    ),
  );
  const request = fetcher.mock.calls.find(
    ([, init]) => init?.method === "POST",
  )!;
  expect(JSON.parse(String(request[1]?.body))).toEqual({
    base_revision: 8,
    approved: true,
    actual_amount: "0",
    currency: "USD",
  });
});
