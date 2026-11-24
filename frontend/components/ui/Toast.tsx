"use client";

import { useEffect, useState } from "react";
import * as ToastPrimitive from "@radix-ui/react-toast";
import { cn } from "@/lib/utils";

interface ToastMessage {
  id: string;
  title: string;
  description?: string;
  variant?: "default" | "destructive";
}

let _listeners: ((msg: ToastMessage) => void)[] = [];

export function toast(msg: Omit<ToastMessage, "id">) {
  const id = Math.random().toString(36).slice(2);
  _listeners.forEach((fn) => fn({ ...msg, id }));
}

export function ToastContainer() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  useEffect(() => {
    const handler = (msg: ToastMessage) => {
      setToasts((prev) => [...prev, msg]);
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== msg.id));
      }, 4000);
    };
    _listeners.push(handler);
    return () => {
      _listeners = _listeners.filter((fn) => fn !== handler);
    };
  }, []);

  return (
    <ToastPrimitive.Provider swipeDirection="right">
      {toasts.map((t) => (
        <ToastPrimitive.Root
          key={t.id}
          className={cn(
            "flex items-start gap-3 rounded-lg border p-4 shadow-lg",
            "bg-white border-gray-200",
            t.variant === "destructive" && "border-red-300 bg-red-50"
          )}
        >
          <div className="flex-1">
            <ToastPrimitive.Title
              className={cn(
                "text-sm font-semibold",
                t.variant === "destructive" ? "text-red-800" : "text-gray-900"
              )}
            >
              {t.title}
            </ToastPrimitive.Title>
            {t.description && (
              <ToastPrimitive.Description className="mt-0.5 text-sm text-gray-600">
                {t.description}
              </ToastPrimitive.Description>
            )}
          </div>
          <ToastPrimitive.Close className="text-gray-400 hover:text-gray-600">
            ✕
          </ToastPrimitive.Close>
        </ToastPrimitive.Root>
      ))}
      <ToastPrimitive.Viewport className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-80 max-w-full" />
    </ToastPrimitive.Provider>
  );
}
