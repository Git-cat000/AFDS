import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "../src/App";
import { framesToTime } from "../src/format";

afterEach(cleanup);

describe("film time", () => {
  it("formats frame durations using the project rational rate", () => {
    expect(framesToTime(480, { fps_num: 24, fps_den: 1 })).toBe("00:20.0");
    expect(framesToTime(30000, { fps_num: 30000, fps_den: 1001 })).toBe(
      "16:41.0",
    );
  });
});

it("offers the offline project creation form", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <App />
    </QueryClientProvider>,
  );
  expect(screen.getByRole("button", { name: "创建项目" })).toBeTruthy();
  expect(screen.getByLabelText("项目名称")).toBeTruthy();
  expect(screen.getByText(/离线 Mock 工作室/)).toBeTruthy();
});
