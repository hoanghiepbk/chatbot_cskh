import { useCallback, useEffect, useState } from "react";
import type { PublicMessage } from "../api/types";
import { supabase } from "./supabase";

// Subscribe to postgres_changes on a table and fire `onChange` on any event.
// Anon RLS gates payload delivery: `conversations` pushes for the anon role
// (real-time mode flips / new conversations), while `tickets`/`messages` only
// push once staff hold a Supabase Auth session (deferred to a future TIP). Pages
// therefore ALSO poll the staff API — this hook is the low-latency bonus,
// polling is the guarantee. No-op when Supabase is unconfigured.
export function useRealtime(
  table: "conversations" | "tickets" | "messages",
  onChange: () => void,
  filter?: string,
): void {
  useEffect(() => {
    const client = supabase;
    if (!client) return;
    const channel = client
      .channel(`rt-${table}-${filter ?? "all"}`)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table, ...(filter ? { filter } : {}) },
        () => onChange(),
      )
      .subscribe();
    return () => {
      void client.removeChannel(channel);
    };
    // onChange intentionally excluded — callers pass a stable callback; we don't
    // want to tear down/re-subscribe on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [table, filter]);
}

// Read masked messages for one conversation from the anon-readable
// messages_public view (live chat history). The base `messages` table is not
// anon-readable for realtime push, so we poll every 3s while the panel is open.
export function useMessages(conversationId: string | null) {
  const [messages, setMessages] = useState<PublicMessage[]>([]);
  const [available, setAvailable] = useState<boolean>(Boolean(supabase));

  const load = useCallback(async () => {
    if (!supabase || !conversationId) return;
    const { data, error } = await supabase
      .from("messages_public")
      .select("id, conversation_id, sender, content_masked, created_at")
      .eq("conversation_id", conversationId)
      .order("created_at", { ascending: true });
    if (error) {
      setAvailable(false);
      return;
    }
    setMessages((data as PublicMessage[]) ?? []);
  }, [conversationId]);

  useEffect(() => {
    if (!supabase || !conversationId) {
      setAvailable(Boolean(supabase));
      setMessages([]);
      return;
    }
    setAvailable(true);
    void load();
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [load, conversationId]);

  return { messages, available, reload: load };
}
