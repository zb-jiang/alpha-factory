import { defineStore } from 'pinia'
import { ref } from 'vue'
import { login as loginApi } from '../api/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const username = ref(localStorage.getItem('username') || '')
  const userId = ref(Number(localStorage.getItem('userId')) || 0)

  async function login(user: string, password: string) {
    const res = await loginApi({ username: user, password })
    token.value = res.data.token
    username.value = res.data.username
    userId.value = res.data.userId
    localStorage.setItem('token', res.data.token)
    localStorage.setItem('username', res.data.username)
    localStorage.setItem('userId', String(res.data.userId))
  }

  function logout() {
    token.value = ''
    username.value = ''
    userId.value = 0
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    localStorage.removeItem('userId')
  }

  function isLoggedIn() {
    return !!token.value
  }

  return { token, username, userId, login, logout, isLoggedIn }
})
