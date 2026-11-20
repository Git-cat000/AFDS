import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "./api";
import type {
  Capability,
  RouteDecision,
  RoutingCandidate,
  RoutingPolicy,
} from "./routing-types";

export type RoutingInventory = {
  revision: number;
  policies: Partial<Record<Capability, RoutingPolicy>>;
  candidates: RoutingCandidate[];
};

export function useRoutingInventory(projectId: string) {
  return useQuery({
    queryKey: ["routing", projectId],
    queryFn: () => api<RoutingInventory>(`/projects/${projectId}/routing`),
  });
}

export function prepareRoute(
  project: { project_id: string; revision: number },
  capability: Capability,
  options: {
    providerId?: string;
    allowRemote: boolean;
    units?: number;
    referenceRoles?: string[];
    remoteOnly?: boolean;
  },
) {
  return api<RouteDecision>(`/projects/${project.project_id}/routing/preview`, {
    base_revision: project.revision,
    capability,
    provider_id: options.providerId,
    allow_remote: options.allowRemote,
    requirements: {
      units: options.units || 1,
      timestamps: capability === "transcription",
      reference_roles: options.referenceRoles || [],
      remote_only: options.remoteOnly || false,
    },
  });
}

type PendingRequest = {
  signature: string;
  id: string;
  previewId?: string;
  version?: number;
  legacy?: boolean;
};

export async function queueRoutedRequest<T>({
  project,
  path,
  body,
  capability,
  requirements = {},
  storageKey,
}: {
  project: { project_id: string; revision: number };
  path: string;
  body: Record<string, unknown>;
  capability: Capability;
  requirements?: {
    units?: number;
    referenceRoles?: string[];
    remoteOnly?: boolean;
    providerId?: string;
  };
  storageKey?: string;
}): Promise<T> {
  const key = storageKey || `afds-model-request-${project.project_id}-${path}`;
  let pending: PendingRequest | undefined;
  let previous: Record<string, unknown> | undefined;
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "null");
    if (typeof value?.id === "string" && typeof value?.signature === "string") {
      pending = value;
      const parsed = JSON.parse(value.signature);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
        previous = parsed;
    }
  } catch {
    /* Discard obsolete browser state. */
  }
  const matches =
    !!previous &&
    JSON.stringify({ ...previous, base_revision: 0 }) ===
      JSON.stringify({ ...body, base_revision: 0 });
  const submitted = matches && previous ? previous : body;
  const id = matches && pending ? pending.id : crypto.randomUUID();
  let previewId = matches ? pending?.previewId : undefined;
  const legacy =
    !!matches &&
    (!!pending?.legacy || (!pending?.version && !pending?.previewId));
  const signature = JSON.stringify(submitted);
  function persist() {
    sessionStorage.setItem(
      key,
      JSON.stringify({ signature, id, previewId, version: 1, legacy }),
    );
  }
  persist();
  try {
    if (
      !legacy &&
      !previewId &&
      !submitted.route_preview_id &&
      (submitted.allow_remote || submitted.provider_id === "auto")
    ) {
      const providerId =
        typeof submitted.provider_id === "string"
          ? submitted.provider_id
          : requirements.providerId;
      const route = await prepareRoute(
        { ...project, revision: Number(submitted.base_revision) },
        capability,
        {
          ...requirements,
          providerId,
          allowRemote: !!submitted.allow_remote,
        },
      );
      previewId = route.id;
      persist();
    }
    const result = await api<T>(path, {
      ...submitted,
      request_id: id,
      ...(previewId ? { route_preview_id: previewId } : {}),
    });
    sessionStorage.removeItem(key);
    return result;
  } catch (error) {
    if (
      error instanceof ApiError &&
      ["ROUTE_PREVIEW_STALE", "REVISION_CONFLICT"].includes(error.code)
    )
      sessionStorage.removeItem(key);
    throw error;
  }
}
