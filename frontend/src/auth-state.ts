import { createContext, useContext } from 'react'
export type Identity = { user: { id: string; username: string }; tenant_id: string; tenants: { id: string; name: string; role: string }[]; csrf_token: string }
export type AuthState = { status: 'loading' | 'anonymous' | 'ready' | 'error'; identity: Identity | null }
export type Context = AuthState & { update: (identity: Identity | null) => void; reload: () => void; logout: () => Promise<void>; switchTenant: (id: string) => Promise<void>; epoch: number }
export const AuthContext = createContext<Context | null>(null)
export function useAuth() { const value = useContext(AuthContext); if (!value) throw new Error('AuthProvider required'); return value }
