"use client";
import { useState } from "react";
import { AppShell } from "@/components/app-shell";
import { Card, PageHeader, Toast } from "@/components/ui";
import { currentUser } from "@/lib/demo-data";
export default function ProfilePage() {
  const [saved, setSaved] = useState(false);
  return (
    <AppShell surface="user" title="Profile">
      <PageHeader
        title="Your profile"
        description="The details your team sees in the Kaia Home workspace."
        actions={
          <button
            className="button button-primary"
            onClick={() => setSaved(true)}
          >
            Save changes
          </button>
        }
      />
      <div className="grid grid-2">
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Personal details</h2>
              <p>Keep your contact information current.</p>
            </div>
          </div>
          <div
            style={{
              display: "flex",
              gap: 16,
              alignItems: "center",
              marginBottom: 10,
            }}
          >
            <span
              className="avatar"
              style={{ width: 64, height: 64, fontSize: 20 }}
            >
              {currentUser.initials}
            </span>
            <button className="button button-secondary button-small">
              Change photo
            </button>
          </div>
          <div className="field">
            <label htmlFor="name">Full name</label>
            <input
              id="name"
              className="input"
              defaultValue={currentUser.name}
            />
          </div>
          <div className="field">
            <label htmlFor="email">Email address</label>
            <input
              id="email"
              className="input"
              type="email"
              defaultValue={currentUser.email}
            />
          </div>
          <div className="field">
            <label htmlFor="phone">Approved WhatsApp number</label>
            <input
              id="phone"
              className="input"
              value={currentUser.phone}
              disabled
            />
            <span className="field-help">
              Phone changes require verification and owner approval.
            </span>
          </div>
        </Card>
        <div className="grid">
          <Card padded>
            <div className="card-head">
              <div>
                <h2>Workspace</h2>
                <p>Your active business context.</p>
              </div>
            </div>
            <div className="detail-list">
              <div className="detail-row">
                <span className="detail-label">Business</span>
                <span className="detail-value">Kaia Home</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Role</span>
                <span className="detail-value">Owner</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Timezone</span>
                <span className="detail-value">Africa/Lagos</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Currency</span>
                <span className="detail-value">Nigerian naira (NGN)</span>
              </div>
            </div>
          </Card>
          <Card padded>
            <div className="card-head">
              <div>
                <h2>Session security</h2>
                <p>Keep access limited to devices you recognise.</p>
              </div>
            </div>
            <div className="alert alert-success">
              ✓ This device · London, United Kingdom · Active now
            </div>
            <button className="button button-secondary">
              Sign out other devices
            </button>
          </Card>
        </div>
      </div>
      {saved && (
        <Toast
          message="Profile changes saved."
          onClose={() => setSaved(false)}
        />
      )}
    </AppShell>
  );
}
