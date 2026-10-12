import { http } from '@/lib/http'

export interface AccountItem {
  name: string
  path: string
  profile_directory?: string
  email?: string
}

export interface AccountDetail extends AccountItem {
  content: string
}

export async function listAccounts(): Promise<AccountItem[]> {
  const status = await http('/api/browser/status')
  if (status.backend === 'native_chrome') {
    const { profiles } = await http('/api/browser/profiles')
    return profiles.map((profile: { name: string; directory: string; path: string; email: string }) => ({
      name: profile.name, path: profile.path, profile_directory: profile.directory, email: profile.email,
    }))
  }
  return await http('/api/accounts')
}

export async function openChromeAccount(directory: string): Promise<void> {
  await http('/api/browser/setup', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ launch: true, profile_directory: directory }),
  })
}

export async function getAccount(name: string): Promise<AccountDetail> {
  return await http(`/api/accounts/${encodeURIComponent(name)}`)
}

export async function createAccount(payload: { name: string; content: string }): Promise<AccountDetail> {
  return await http('/api/accounts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function updateAccount(name: string, content: string): Promise<AccountDetail> {
  return await http(`/api/accounts/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
}

export async function deleteAccount(name: string): Promise<{ message: string }> {
  return await http(`/api/accounts/${encodeURIComponent(name)}`, { method: 'DELETE' })
}
