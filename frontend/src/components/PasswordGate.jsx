import { useState, useEffect, useRef } from 'react'

export default function PasswordGate({ children }) {
  const [authenticated, setAuthenticated] = useState(false)
  const [visible, setVisible] = useState(false)
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [contentOpacity, setContentOpacity] = useState(0)
  const inputRef = useRef(null)

  useEffect(() => {
    if (sessionStorage.getItem('baseline_authenticated') === 'true') {
      setAuthenticated(true)
      setContentOpacity(1)
    } else {
      setVisible(true)
    }
  }, [])

  useEffect(() => {
    if (visible && inputRef.current) {
      inputRef.current.focus()
    }
  }, [visible])

  const APP_PASSWORD = (import.meta.env.VITE_APP_PASSWORD || '').replace(/^﻿/, '').trim()

  const handleSubmit = () => {
    if (password === APP_PASSWORD) {
      sessionStorage.setItem('baseline_authenticated', 'true')
      setVisible(false)
      setAuthenticated(true)
      setTimeout(() => setContentOpacity(1), 50)
    } else {
      setError('Incorrect password. Try again.')
      setPassword('')
      if (inputRef.current) inputRef.current.focus()
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleSubmit()
  }

  if (authenticated) {
    return (
      <div style={{ opacity: contentOpacity, transition: 'opacity 0.5s ease' }}>
        {children}
      </div>
    )
  }

  if (!visible) return null

  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      backgroundImage: 'url(/baseline-bg.png)',
      backgroundSize: 'cover',
      backgroundPosition: 'center',
      backgroundRepeat: 'no-repeat',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 9999,
    }}>
      {/* Dark overlay so form stays readable */}
      <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.55)' }} />
      <div style={{ position: 'relative', zIndex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <img
        src="/baseline-logo.png"
        alt="Baseline"
        style={{ height: 60, width: 'auto' }}
      />
      <div style={{
        fontSize: 13,
        color: '#555',
        letterSpacing: '0.12em',
        textTransform: 'uppercase',
        marginTop: 12,
        marginBottom: 32,
      }}>
        Tennis Prop Analytics
      </div>

      <input
        ref={inputRef}
        type="password"
        placeholder="Enter access password"
        value={password}
        onChange={(e) => {
          setPassword(e.target.value)
          setError('')
        }}
        onKeyDown={handleKeyDown}
        style={{
          width: 280,
          padding: '12px 16px',
          background: '#111111',
          border: error ? '1px solid #FF4444' : '1px solid #1e1e1e',
          borderRadius: 8,
          color: 'white',
          fontSize: 14,
          outline: 'none',
          marginBottom: 12,
          boxSizing: 'border-box',
        }}
        onFocus={(e) => {
          if (!error) e.target.style.border = '1px solid #00E676'
        }}
        onBlur={(e) => {
          if (!error) e.target.style.border = '1px solid #1e1e1e'
        }}
      />

      <button
        onClick={handleSubmit}
        style={{
          width: 280,
          padding: 12,
          background: '#00E676',
          color: 'black',
          fontWeight: 700,
          fontSize: 14,
          border: 'none',
          borderRadius: 8,
          cursor: 'pointer',
          marginBottom: 12,
          boxSizing: 'border-box',
        }}
      >
        Enter
      </button>

      <div style={{
        color: '#FF4444',
        fontSize: 13,
        height: 20,
      }}>
        {error}
      </div>
      </div>
    </div>
  )
}
