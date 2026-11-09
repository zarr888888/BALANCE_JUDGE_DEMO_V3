import { afterEach, expect, test, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App.jsx'

afterEach(()=>{cleanup();vi.unstubAllGlobals()})

test('public demo credentials remain visible with the existing judge and operator forms',async()=>{
  vi.stubGlobal('fetch',vi.fn(async path=>({ok:true,json:async()=>path==='/api/session'?{authenticated:false,role:null}:{status:'READY'}})))
  const user=userEvent.setup()
  render(<App/> )
  await screen.findByText('Backend: READY')
  const panel=screen.getByRole('region',{name:'Akses Demo Penilaian'})
  expect(within(panel).getByText('juri')).toBeTruthy()
  expect(within(panel).getByText('BALANCE-Demo2026!')).toBeTruthy()
  expect(within(panel).getByText('Gunakan akun ini untuk mencoba prediksi, optimasi, dan replay dengan data SIMULASI.')).toBeTruthy()
  expect(screen.getByLabelText('Username').value).toBe('juri')
  expect(screen.getByLabelText('Password').type).toBe('password')
  expect(screen.queryByText(/disertakan dalam pengumpulan lomba/)).toBeNull()
  await user.click(screen.getByRole('button',{name:'Gunakan kunci operator'}))
  expect(screen.getByLabelText('Kunci dasbor').type).toBe('password')
  expect(screen.getByLabelText('Kunci dasbor').value).toBe('')
  expect(screen.getByRole('region',{name:'Akses Demo Penilaian'})).toBeTruthy()
  await user.click(screen.getByRole('button',{name:'Gunakan akun demo juri'}))
  expect(screen.getByLabelText('Password').value).toBe('')
})