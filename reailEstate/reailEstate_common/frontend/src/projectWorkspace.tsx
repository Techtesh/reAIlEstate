import { createContext, useContext } from 'react'
import { api, apiPath } from './api'

const adminPrefix = '/api/v1/admin/project-workspace/'
function adminPath(path: string) {
  const local = apiPath(path)
  return local.startsWith(adminPrefix) ? local : adminPrefix + local.slice('/api/v1/'.length)
}
const builderWorkspace = { admin: false, api, path: (path: string) => path }
const adminWorkspace = {
  admin: true,
  api: <Result,>(path: string, options?: RequestInit) => api<Result>(adminPath(path), options),
  path: adminPath,
}
export const ProjectWorkspaceContext = createContext(builderWorkspace)
export const platformProjectWorkspace = adminWorkspace
export function useProjectWorkspace() { return useContext(ProjectWorkspaceContext) }
