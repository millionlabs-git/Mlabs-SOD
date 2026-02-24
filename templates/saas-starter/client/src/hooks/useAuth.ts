import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";
import type { User } from "@shared/types";

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchUser = useCallback(async () => {
    try {
      const data = await api.get<User>("/auth/me");
      setUser(data);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUser();
  }, [fetchUser]);

  const login = async (username: string, password: string) => {
    const data = await api.post<User>("/auth/login", { username, password });
    setUser(data);
    return data;
  };

  const logout = async () => {
    await api.post("/auth/logout", {});
    setUser(null);
  };

  return { user, loading, login, logout, refetch: fetchUser };
}
