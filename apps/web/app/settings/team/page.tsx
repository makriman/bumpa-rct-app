"use client";

import { PlusIcon } from "@phosphor-icons/react";
import { useMemo, useReducer } from "react";
import { AppShell } from "@/components/app-shell";
import { LiveDataBanner } from "@/components/live-data-banner";
import { TeamDialogs, type TeamFormState } from "@/components/team-dialogs";
import {
  Badge,
  Filters,
  PageHeader,
  ScrollableTable,
  StatePanel,
  Toast,
} from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { workspaceRoleLabel } from "@/lib/consumer-data";
import { maskPhone, titleCase, type TeamMember } from "@/lib/platform-data";
import { useApiResource } from "@/lib/use-api-resource";

type TeamState = {
  busy: boolean;
  error: string;
  form: TeamFormState;
  inviteOpen: boolean;
  query: string;
  removing: TeamMember | null;
  status: string;
  toast: string;
};

type TeamAction = { type: "patch"; value: Partial<TeamState> };

const EMPTY_FORM: TeamFormState = {
  email: "",
  name: "",
  phone: "",
  role: "member",
};

const initialState: TeamState = {
  busy: false,
  error: "",
  form: EMPTY_FORM,
  inviteOpen: false,
  query: "",
  removing: null,
  status: "all",
  toast: "",
};

function teamReducer(state: TeamState, action: TeamAction): TeamState {
  return action.type === "patch" ? { ...state, ...action.value } : state;
}

function memberInitials(name: string): string {
  return name
    .split(" ")
    .flatMap((part) => (part[0] ? [part[0]] : []))
    .slice(0, 2)
    .join("");
}

export default function TeamPage() {
  const resource = useApiResource<TeamMember[]>("/settings/team");
  const [state, dispatch] = useReducer(teamReducer, initialState);
  const rows = useMemo(
    () =>
      (resource.data ?? []).filter((member) => {
        const matchesText =
          `${member.name} ${member.email ?? ""} ${member.phone_e164}`
            .toLowerCase()
            .includes(state.query.toLowerCase());
        return (
          matchesText &&
          (state.status === "all" || member.status === state.status)
        );
      }),
    [resource.data, state.query, state.status],
  );

  const invite = async () => {
    dispatch({ type: "patch", value: { busy: true, error: "" } });
    try {
      await apiRequest("/settings/team", {
        method: "POST",
        body: JSON.stringify({
          name: state.form.name,
          phone_e164: state.form.phone,
          email: state.form.email || null,
          role: state.form.role,
        }),
      });
      await resource.reload();
      dispatch({
        type: "patch",
        value: {
          inviteOpen: false,
          form: EMPTY_FORM,
          toast: "Team member added to this workspace.",
        },
      });
    } catch (reason) {
      dispatch({
        type: "patch",
        value: {
          error:
            reason instanceof Error
              ? reason.message
              : "The team member could not be added.",
        },
      });
    } finally {
      dispatch({ type: "patch", value: { busy: false } });
    }
  };

  const remove = async () => {
    if (!state.removing) return;
    dispatch({ type: "patch", value: { busy: true, error: "" } });
    try {
      await apiRequest(`/settings/team/${state.removing.membership_id}`, {
        method: "DELETE",
      });
      await resource.reload();
      dispatch({
        type: "patch",
        value: { removing: null, toast: `${state.removing.name} was removed.` },
      });
    } catch (reason) {
      dispatch({
        type: "patch",
        value: {
          error:
            reason instanceof Error
              ? reason.message
              : "The member could not be removed.",
        },
      });
    } finally {
      dispatch({ type: "patch", value: { busy: false } });
    }
  };

  return (
    <AppShell title="Team">
      <PageHeader
        title="Team access"
        description="Invite trusted people and manage persisted workspace memberships."
        actions={
          <button
            type="button"
            className="button button-primary"
            disabled={resource.source !== "live" || state.busy}
            title={
              resource.source !== "live"
                ? "Team changes require a live API response."
                : undefined
            }
            onClick={() =>
              dispatch({
                type: "patch",
                value: { inviteOpen: true, error: "" },
              })
            }
          >
            <PlusIcon aria-hidden="true" /> Add teammate
          </button>
        }
      />
      <LiveDataBanner
        label="team memberships"
        source={resource.source}
        status={resource.status}
        count={resource.data?.length}
        error={resource.error}
      />
      <div className="alert alert-info">
        Only owners and managers can change access. The API records every
        membership addition and removal.
      </div>
      {state.error && !state.inviteOpen && !state.removing && (
        <div className="alert alert-danger" role="alert">
          {state.error}
        </div>
      )}
      {resource.status === "loading" ? (
        <StatePanel type="loading" />
      ) : resource.status === "error" ? (
        <StatePanel
          type="error"
          description={resource.error ?? undefined}
          action={
            <button
              type="button"
              className="button button-secondary"
              onClick={() => void resource.reload()}
            >
              Try again
            </button>
          }
        />
      ) : !resource.data?.length ? (
        <StatePanel
          type="empty"
          title="No team members returned"
          description="Add the first member when authenticated as a workspace owner or manager."
        />
      ) : (
        <TeamTable
          busy={state.busy}
          members={rows}
          onRemove={(member) =>
            dispatch({
              type: "patch",
              value: { removing: member, error: "" },
            })
          }
          onQueryChange={(query) =>
            dispatch({ type: "patch", value: { query } })
          }
          onStatusChange={(status) =>
            dispatch({ type: "patch", value: { status } })
          }
          query={state.query}
          source={resource.source}
          status={state.status}
        />
      )}
      <TeamDialogs
        busy={state.busy}
        error={state.error}
        form={state.form}
        inviteOpen={state.inviteOpen}
        onCloseInvite={() =>
          !state.busy &&
          dispatch({ type: "patch", value: { inviteOpen: false, error: "" } })
        }
        onCloseRemove={() =>
          !state.busy &&
          dispatch({ type: "patch", value: { removing: null, error: "" } })
        }
        onFormChange={(value) =>
          dispatch({
            type: "patch",
            value: { form: { ...state.form, ...value } },
          })
        }
        onInvite={invite}
        onRemove={remove}
        removing={state.removing}
      />
      {state.toast && (
        <Toast
          message={state.toast}
          onClose={() => dispatch({ type: "patch", value: { toast: "" } })}
        />
      )}
    </AppShell>
  );
}

function TeamTable({
  busy,
  members,
  onQueryChange,
  onRemove,
  onStatusChange,
  query,
  source,
  status,
}: {
  busy: boolean;
  members: TeamMember[];
  onQueryChange: (value: string) => void;
  onRemove: (member: TeamMember) => void;
  onStatusChange: (value: string) => void;
  query: string;
  source: "live" | null;
  status: string;
}) {
  return (
    <>
      <Filters search={query} setSearch={onQueryChange}>
        <select
          className="filter-select"
          aria-label="Filter by status"
          value={status}
          onChange={(event) => onStatusChange(event.target.value)}
        >
          <option value="all">All statuses</option>
          <option value="active">Active</option>
          <option value="revoked">Revoked</option>
        </select>
      </Filters>
      {members.length ? (
        <ScrollableTable className="card" label="Workspace team members">
          <table className="data-table">
            <thead>
              <tr>
                <th>Person</th>
                <th>Contact</th>
                <th>Role</th>
                <th>Status</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <tr key={member.membership_id}>
                  <td>
                    <div
                      style={{ display: "flex", gap: 10, alignItems: "center" }}
                    >
                      <span className="avatar">
                        {memberInitials(member.name)}
                      </span>
                      <span>
                        <span className="table-primary">{member.name}</span>
                        {member.email && (
                          <span className="table-secondary">
                            {member.email}
                          </span>
                        )}
                      </span>
                    </div>
                  </td>
                  <td>{maskPhone(member.phone_e164)}</td>
                  <td>{workspaceRoleLabel(member.role)}</td>
                  <td>
                    <Badge>{titleCase(member.status)}</Badge>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="button button-ghost button-small"
                      disabled={
                        member.role === "owner" ||
                        member.status !== "active" ||
                        source !== "live" ||
                        busy
                      }
                      onClick={() => onRemove(member)}
                    >
                      {member.role === "owner" ? "Owner protected" : "Remove"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollableTable>
      ) : (
        <StatePanel
          type="empty"
          title="No matching team members"
          description="Clear or adjust the filters."
        />
      )}
    </>
  );
}
