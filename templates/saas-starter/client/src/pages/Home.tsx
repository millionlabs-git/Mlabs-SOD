import { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function Home() {
  const [health, setHealth] = useState<{ status: string } | null>(null);

  useEffect(() => {
    api.get<{ status: string }>("/health").then(setHealth).catch(() => {});
  }, []);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight text-gray-900">
          Welcome to your SaaS App
        </h1>
        <p className="mt-2 text-lg text-gray-600">
          This is your starting point. Customize it based on your PRD.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="text-lg font-semibold text-gray-900">React Frontend</h3>
          <p className="mt-2 text-sm text-gray-600">
            Vite + React 19 + Tailwind CSS with wouter for routing.
          </p>
        </div>
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="text-lg font-semibold text-gray-900">Express API</h3>
          <p className="mt-2 text-sm text-gray-600">
            Express backend with session auth and structured route registration.
          </p>
        </div>
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="text-lg font-semibold text-gray-900">PostgreSQL + Drizzle</h3>
          <p className="mt-2 text-sm text-gray-600">
            Type-safe database with Drizzle ORM and PostgreSQL.
          </p>
        </div>
      </div>

      {health && (
        <p className="text-sm text-green-600">
          API Status: {health.status}
        </p>
      )}
    </div>
  );
}
