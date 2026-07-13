/**
 * Compile-time guards between the hand-named UI models and the generated API
 * schemas. This module emits no JavaScript; `npm run typecheck` fails whenever
 * either side changes without the other remaining structurally compatible.
 */
import type { components } from "./generated/api-contract";
import type {
  AdminExport,
  AsyncJob,
  HermesCallError,
  McpAdminConnection,
  McpConnection,
  PlatformAdmin,
  Report,
  TenantOnboarding,
  TenantOperations,
  WhatsAppDeliveryFailure,
} from "./platform-data";

type Schemas = components["schemas"];
type Compatible<Ui, Api> = [Ui] extends [Api]
  ? [Api] extends [Ui]
    ? true
    : false
  : false;
type Assert<T extends true> = T;

type ContractAssertions = [
  Assert<Compatible<PlatformAdmin, Schemas["PlatformAdminView"]>>,
  Assert<Compatible<TenantOnboarding, Schemas["OnboardingView"]>>,
  Assert<Compatible<TenantOperations, Schemas["TenantOperationsView"]>>,
  Assert<
    Compatible<WhatsAppDeliveryFailure, Schemas["WhatsappDeliveryFailureView"]>
  >,
  Assert<Compatible<HermesCallError, Schemas["HermesCallErrorView"]>>,
  Assert<Compatible<AdminExport, Schemas["AdminExportView"]>>,
  Assert<Compatible<AsyncJob, Schemas["AsyncJobView"]>>,
  Assert<Compatible<Report, Schemas["ReportView"]>>,
  Assert<Compatible<McpConnection, Schemas["McpConnectionView"]>>,
  Assert<Compatible<McpAdminConnection, Schemas["McpAdminConnectionView"]>>,
];

// Retain the aggregate so TypeScript evaluates every assertion under `noEmit`.
export type ApiContractAssertions = ContractAssertions;
