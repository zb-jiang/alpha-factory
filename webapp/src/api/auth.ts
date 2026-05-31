import api from './index'

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  token: string
  username: string
  userId: number
}

export function login(data: LoginRequest) {
  return api.post<any, { data: LoginResponse }>('/auth/login', data)
}

export function register(data: LoginRequest) {
  return api.post('/auth/register', data)
}

export function getMe() {
  return api.get('/auth/me')
}
