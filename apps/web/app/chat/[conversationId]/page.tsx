import { ChatWorkspace } from "@/components/chat/chat-workspace";

export default async function ConversationPage({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { conversationId } = await params;
  return <ChatWorkspace initialConversationId={conversationId} />;
}
