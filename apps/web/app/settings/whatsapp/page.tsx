"use client";

import { PlusIcon } from "@phosphor-icons/react";
import { useReducer } from "react";
import { AppIcon } from "@/components/app-icon";
import { AppShell } from "@/components/app-shell";
import { LiveDataBanner } from "@/components/live-data-banner";
import { Badge, PageHeader, StatePanel, Toast } from "@/components/ui";
import { WhatsAppDialogs } from "@/components/whatsapp-dialogs";
import { apiRequest } from "@/lib/api";
import {
  maskPhone,
  titleCase,
  type TeamMember,
  type WhatsAppNumber,
} from "@/lib/platform-data";
import { useApiResource } from "@/lib/use-api-resource";

type WhatsAppState = {
  modal: boolean;
  userId: string;
  phone: string;
  label: string;
  busy: boolean;
  error: string;
  toast: string;
  removing: WhatsAppNumber | null;
};

type WhatsAppAction = { type: "patch"; value: Partial<WhatsAppState> };

const initialState: WhatsAppState = {
  modal: false,
  userId: "",
  phone: "",
  label: "",
  busy: false,
  error: "",
  toast: "",
  removing: null,
};

function whatsappReducer(
  state: WhatsAppState,
  action: WhatsAppAction,
): WhatsAppState {
  return action.type === "patch" ? { ...state, ...action.value } : state;
}

export default function WhatsAppPage() {
  const numbers = useApiResource<WhatsAppNumber[]>(
    "/settings/whatsapp-numbers",
  );
  const team = useApiResource<TeamMember[]>("/settings/team");
  const [state, dispatch] = useReducer(whatsappReducer, initialState);
  const add = async () => {
    dispatch({ type: "patch", value: { busy: true, error: "" } });
    try {
      await apiRequest("/settings/whatsapp-numbers", {
        method: "POST",
        body: JSON.stringify({
          user_id: state.userId,
          phone_e164: state.phone,
          label: state.label || null,
        }),
      });
      await numbers.reload();
      dispatch({
        type: "patch",
        value: {
          modal: false,
          userId: "",
          phone: "",
          label: "",
          toast: "Approved phone identity added.",
        },
      });
    } catch (reason) {
      dispatch({
        type: "patch",
        value: {
          error:
            reason instanceof Error
              ? reason.message
              : "The phone identity could not be added.",
        },
      });
    } finally {
      dispatch({ type: "patch", value: { busy: false } });
    }
  };
  const remove = async () => {
    if (!state.removing || state.busy) return;
    dispatch({ type: "patch", value: { busy: true, error: "" } });
    try {
      await apiRequest(`/settings/whatsapp-numbers/${state.removing.id}`, {
        method: "DELETE",
      });
      await numbers.reload();
      dispatch({
        type: "patch",
        value: {
          removing: null,
          toast: "WhatsApp access removed for that team identity.",
        },
      });
    } catch (reason) {
      dispatch({
        type: "patch",
        value: {
          error:
            reason instanceof Error
              ? reason.message
              : "The phone identity could not be removed.",
        },
      });
    } finally {
      dispatch({ type: "patch", value: { busy: false } });
    }
  };
  const availableMembers = (team.data ?? []).filter(
    (member) => member.status === "active",
  );
  return (
    <AppShell title="WhatsApp numbers">
      <PageHeader
        title="Approved WhatsApp numbers"
        description="Review phone identities authorised to access this workspace."
        actions={
          <button
            type="button"
            className="button button-primary"
            disabled={
              numbers.source !== "live" ||
              team.source !== "live" ||
              !availableMembers.length ||
              state.busy
            }
            onClick={() =>
              dispatch({ type: "patch", value: { modal: true, error: "" } })
            }
          >
            <PlusIcon aria-hidden="true" /> Add number
          </button>
        }
      />
      <LiveDataBanner
        label="approved phone identities"
        source={numbers.source}
        status={numbers.status}
        count={numbers.data?.length}
        error={numbers.error}
      />
      <div className="alert alert-info">
        Unknown numbers are rejected by the webhook. STOP and START update the
        recorded opt-out state after the live Meta webhook is activated.
      </div>
      {state.error && (
        <div className="alert alert-danger" role="alert">
          {state.error}
        </div>
      )}
      {numbers.status === "loading" ? (
        <StatePanel type="loading" />
      ) : numbers.status === "error" ? (
        <StatePanel
          type="error"
          description={numbers.error ?? undefined}
          action={
            <button
              type="button"
              className="button button-secondary"
              onClick={() => void numbers.reload()}
            >
              Try again
            </button>
          }
        />
      ) : !numbers.data?.length ? (
        <StatePanel
          type="empty"
          title="No approved numbers"
          description="Add an active team member's phone identity when authenticated as an owner or manager."
        />
      ) : (
        <div className="grid">
          {numbers.data.map((number) => (
            <section className="card connection-card" key={number.id}>
              <div className="connection-icon">
                <AppIcon name="chat" size={22} />
              </div>
              <div className="connection-body">
                <strong>{number.label || "Approved team number"}</strong>
                <p>
                  {maskPhone(number.phone_e164)} · user{" "}
                  {number.user_id.slice(0, 8)}
                </p>
              </div>
              <Badge>
                {number.opt_out ? "Opted out" : titleCase(number.status)}
              </Badge>
              {team.data?.find((member) => member.user_id === number.user_id)
                ?.role === "owner" ? (
                <button
                  type="button"
                  className="button button-ghost button-small"
                  disabled
                  title="Owner mappings are controlled by the workspace owner."
                >
                  Platform managed
                </button>
              ) : (
                <button
                  type="button"
                  className="button button-ghost button-small"
                  disabled={
                    numbers.source !== "live" ||
                    team.status !== "ready" ||
                    state.busy
                  }
                  onClick={() =>
                    dispatch({ type: "patch", value: { removing: number } })
                  }
                >
                  Remove access
                </button>
              )}
            </section>
          ))}
        </div>
      )}
      <WhatsAppDialogs
        availableMembers={availableMembers}
        busy={state.busy}
        label={state.label}
        modalOpen={state.modal}
        onAdd={add}
        onCloseAdd={() =>
          !state.busy && dispatch({ type: "patch", value: { modal: false } })
        }
        onCloseRemove={() =>
          !state.busy && dispatch({ type: "patch", value: { removing: null } })
        }
        onLabelChange={(label) => dispatch({ type: "patch", value: { label } })}
        onPhoneChange={(phone) => dispatch({ type: "patch", value: { phone } })}
        onRemove={remove}
        onUserChange={(userId) =>
          dispatch({ type: "patch", value: { userId } })
        }
        phone={state.phone}
        removing={state.removing}
        userId={state.userId}
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
