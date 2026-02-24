import { Route, Switch } from "wouter";
import Layout from "./components/Layout";
import Home from "./pages/Home";

export default function App() {
  return (
    <Layout>
      <Switch>
        <Route path="/" component={Home} />
        <Route>
          <div className="flex items-center justify-center min-h-[50vh]">
            <h1 className="text-2xl font-semibold text-gray-600">
              404 - Page Not Found
            </h1>
          </div>
        </Route>
      </Switch>
    </Layout>
  );
}
