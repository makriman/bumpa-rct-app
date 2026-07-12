export type Role =
  | "owner"
  | "member"
  | "operator"
  | "researcher"
  | "superadmin";
export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

export const currentUser = {
  name: "Amara Okafor",
  initials: "AO",
  phone: "+234 803 ••• 1442",
  email: "amara@kaiahome.ng",
  role: "owner" as Role,
  tenant: "Kaia Home",
  timezone: "Africa/Lagos",
  currency: "NGN",
};

export const conversations = [
  {
    id: "weekly",
    title: "Best sellers this week",
    preview: "Your Adire Table Runner led…",
    time: "10:42",
  },
  {
    id: "stock",
    title: "Stock to reorder",
    preview: "Three products may run out…",
    time: "Yesterday",
  },
  {
    id: "customers",
    title: "Returning customers",
    preview: "Repeat purchases grew 14%…",
    time: "8 Jul",
  },
  {
    id: "pricing",
    title: "Pricing the linen set",
    preview: "At a 42% gross margin…",
    time: "4 Jul",
  },
];

export const team = [
  {
    name: "Amara Okafor",
    initials: "AO",
    contact: "+234 803 ••• 1442",
    role: "Owner",
    status: "Active",
    lastSeen: "Now",
  },
  {
    name: "Tobi Adeyemi",
    initials: "TA",
    contact: "+234 706 ••• 0901",
    role: "Admin",
    status: "Active",
    lastSeen: "2 hours ago",
  },
  {
    name: "Kemi Bello",
    initials: "KB",
    contact: "+234 812 ••• 5510",
    role: "Member",
    status: "Invited",
    lastSeen: "Invite sent 11 Jul",
  },
];

export const phones = [
  {
    label: "Amara · Owner",
    number: "+234 803 ••• 1442",
    status: "Approved",
    lastSeen: "Today, 10:42",
  },
  {
    label: "Tobi · Operations",
    number: "+234 706 ••• 0901",
    status: "Approved",
    lastSeen: "Yesterday, 16:20",
  },
  {
    label: "Kemi · Sales",
    number: "+234 812 ••• 5510",
    status: "Pending",
    lastSeen: "Not yet active",
  },
];

export const tenants = [
  {
    id: "kaia-home",
    name: "Kaia Home",
    owner: "Amara Okafor",
    category: "Home & living",
    city: "Lagos",
    status: "Active",
    sync: "12 min ago",
    health: "Healthy",
    consent: "Granted",
    users: 3,
  },
  {
    id: "morenike",
    name: "Morenike Studio",
    owner: "Dami Ajayi",
    category: "Fashion",
    city: "Abuja",
    status: "Active",
    sync: "38 min ago",
    health: "Healthy",
    consent: "Granted",
    users: 5,
  },
  {
    id: "bean-there",
    name: "Bean There Coffee",
    owner: "Feyi Cole",
    category: "Food & drink",
    city: "Lagos",
    status: "Active",
    sync: "4 hrs ago",
    health: "Attention",
    consent: "Pending",
    users: 2,
  },
  {
    id: "naya-skin",
    name: "Naya Skin",
    owner: "Ife Nwosu",
    category: "Beauty",
    city: "Port Harcourt",
    status: "Suspended",
    sync: "3 days ago",
    health: "Offline",
    consent: "Withdrawn",
    users: 4,
  },
  {
    id: "ori-crafts",
    name: "Ori Crafts",
    owner: "Lola Akanbi",
    category: "Arts & crafts",
    city: "Ibadan",
    status: "Onboarding",
    sync: "Never",
    health: "Setup",
    consent: "Pending",
    users: 1,
  },
];

export const researchQuestions = [
  {
    time: "12 Jul · 10:42",
    tenant: "SME–K4H2",
    user: "U–104",
    channel: "WhatsApp",
    question: "Which products sold best this week?",
    intent: "Sales analysis",
    help: "Data lookup",
    data: "Products",
    latency: "2.4s",
    flag: "Good",
  },
  {
    time: "12 Jul · 09:18",
    tenant: "SME–M8P1",
    user: "U–088",
    channel: "Web",
    question: "Why did revenue fall after payday?",
    intent: "Finance",
    help: "Diagnosis",
    data: "Mixed",
    latency: "4.7s",
    flag: "Review",
  },
  {
    time: "11 Jul · 18:04",
    tenant: "SME–B2N7",
    user: "U–121",
    channel: "WhatsApp",
    question: "Write a message for customers who have not ordered lately",
    intent: "Marketing",
    help: "Draft message",
    data: "Customers",
    latency: "3.1s",
    flag: "Good",
  },
  {
    time: "11 Jul · 14:51",
    tenant: "SME–K4H2",
    user: "U–105",
    channel: "WhatsApp",
    question: "What should I restock before the weekend?",
    intent: "Inventory",
    help: "Recommendation",
    data: "Mixed",
    latency: "5.2s",
    flag: "Good",
  },
  {
    time: "10 Jul · 12:09",
    tenant: "SME–O3C9",
    user: "U–140",
    channel: "Web",
    question: "Show me customers with more than three orders",
    intent: "Customers",
    help: "Data lookup",
    data: "Customers",
    latency: "2.8s",
    flag: "Good",
  },
];

export const reports = [
  {
    title: "Weekly research memo · W28",
    type: "Weekly memo",
    scope: "All consented SMEs",
    created: "12 Jul 2026",
    status: "Ready",
    formats: "PDF · CSV",
  },
  {
    title: "Question taxonomy · Q2",
    type: "Taxonomy",
    scope: "1 Apr – 30 Jun",
    created: "4 Jul 2026",
    status: "Ready",
    formats: "PDF · JSONL",
  },
  {
    title: "Fashion cohort behaviour",
    type: "Cohort",
    scope: "12 fashion SMEs",
    created: "1 Jul 2026",
    status: "Ready",
    formats: "PDF · CSV",
  },
  {
    title: "Monthly academic memo · June",
    type: "Academic memo",
    scope: "All consented SMEs",
    created: "Processing",
    status: "Running",
    formats: "—",
  },
];

export const syncRuns = [
  {
    tenant: "Kaia Home",
    range: "5 Jul – 12 Jul",
    started: "12 Jul, 10:30",
    duration: "31s",
    datasets: "11/11",
    status: "Success",
  },
  {
    tenant: "Morenike Studio",
    range: "5 Jul – 12 Jul",
    started: "12 Jul, 10:04",
    duration: "44s",
    datasets: "11/11",
    status: "Success",
  },
  {
    tenant: "Bean There Coffee",
    range: "5 Jul – 12 Jul",
    started: "12 Jul, 08:18",
    duration: "1m 12s",
    datasets: "8/11",
    status: "Partial",
  },
  {
    tenant: "Naya Skin",
    range: "3 Jul – 10 Jul",
    started: "10 Jul, 02:00",
    duration: "8s",
    datasets: "0/11",
    status: "Failed",
  },
];

export const errors = [
  {
    severity: "High",
    service: "Bumpa sync",
    tenant: "Naya Skin",
    message: "Authentication rejected by upstream API",
    happened: "10 Jul, 02:00",
    count: 3,
    status: "Open",
  },
  {
    severity: "Medium",
    service: "WhatsApp",
    tenant: "Bean There Coffee",
    message: "Template delivery rejected: recipient unavailable",
    happened: "12 Jul, 08:42",
    count: 1,
    status: "Investigating",
  },
  {
    severity: "Low",
    service: "Hermes",
    tenant: "Morenike Studio",
    message: "Response exceeded latency threshold (12s)",
    happened: "11 Jul, 16:21",
    count: 2,
    status: "Resolved",
  },
];

export const usageRows = [
  {
    tenant: "Kaia Home",
    messages: 184,
    whatsapp: "72%",
    web: "28%",
    llm: "₦6,420",
    active: "3 / 3",
  },
  {
    tenant: "Morenike Studio",
    messages: 142,
    whatsapp: "81%",
    web: "19%",
    llm: "₦4,980",
    active: "4 / 5",
  },
  {
    tenant: "Bean There Coffee",
    messages: 91,
    whatsapp: "92%",
    web: "8%",
    llm: "₦2,744",
    active: "2 / 2",
  },
  {
    tenant: "Ori Crafts",
    messages: 0,
    whatsapp: "—",
    web: "—",
    llm: "₦0",
    active: "0 / 1",
  },
];

export const chartValues = [36, 45, 42, 58, 52, 66, 61, 72, 69, 84, 78, 92];

export function statusTone(status: string): Tone {
  const normalized = status.toLowerCase();
  if (
    [
      "active",
      "approved",
      "healthy",
      "success",
      "ready",
      "good",
      "granted",
      "resolved",
      "connected",
    ].includes(normalized)
  )
    return "success";
  if (
    ["failed", "offline", "suspended", "withdrawn", "high", "open"].includes(
      normalized,
    )
  )
    return "danger";
  if (
    [
      "pending",
      "partial",
      "attention",
      "onboarding",
      "review",
      "medium",
      "investigating",
      "running",
      "setup",
    ].includes(normalized)
  )
    return "warning";
  if (["web", "whatsapp"].includes(normalized)) return "info";
  return "neutral";
}
