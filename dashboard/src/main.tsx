import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { AuthProvider } from './context/AuthContext';
import { RoleViewProvider } from './context/RoleViewContext';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter basename="/dashboard">
      <AuthProvider>
        <RoleViewProvider>
          <App />
        </RoleViewProvider>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
