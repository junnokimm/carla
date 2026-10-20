// Real production LocalizationStage + offline Town04. No Client, RPC or simulator.
#include "carla/client/Map.h"
#include "carla/trafficmanager/LocalizationStage.h"
#include "carla/trafficmanager/Constants.h"
#include <fstream>
#include <algorithm>
#include <iostream>
#include <iterator>
#include <stdexcept>

namespace carla { namespace traffic_manager {
class ParametersTestPeer {
public:
  static void Force(Parameters &p, ActorId id, bool right) {
    p.force_lane_change.AddEntry({id, {true, right}});
  }
  static void NoRandom(Parameters &p, ActorId id) {
    p.auto_lane_change.AddEntry({id, false});
  }
  static void ExitRoute(Parameters &p, ActorId id, bool clear = false, bool through = false) {
    p.custom_route.AddEntry({id, clear ? Route{} : Route{static_cast<uint8_t>(
      through ? RoadOption::Straight : RoadOption::Right)}});
    p.upload_route.AddEntry({id, true});
  }
};
}}
namespace ctm = carla::traffic_manager;
namespace cg = carla::geom;

void Require(bool ok, const char *message) {
  if (!ok) throw std::runtime_error(message);
}

void Run(const ctm::LocalMapPtr &map, float speed, bool right, float offset) {
  std::vector<ctm::ActorId> ids{125};
  ctm::BufferMap buffers;
  ctm::SimulationState state;
  ctm::TrackTraffic traffic;
  ctm::Parameters parameters;
  std::vector<ctm::ActorId> removed;
  ctm::LocalizationFrame output(1);
  ctm::RandomGenerator random(42);
  auto waypoint = map->GetMap().GetWaypointXODR(38, -2, 45.0f + offset);
  auto transform = waypoint->GetTransform();
  ctm::KinematicState motion{transform.location, transform.rotation,
                           transform.GetForwardVector() * speed, 90.0f, true, false, {}};
  state.AddActor(125, motion, {ctm::ActorType::Vehicle, 2.4f, 0.9f, 0.8f},
                 {carla::rpc::TrafficLightState::Green, false});
  ctm::ParametersTestPeer::NoRandom(parameters, 125);
  ctm::LocalizationStage stage(ids, buffers, state, traffic, map, parameters, removed, output, random);
  stage.Update(0);
  // A second update models the actual front-of-buffer purge before the request.
  ctm::ParametersTestPeer::Force(parameters, 125, right);
  stage.Update(0);
  int target = right ? -3 : -1;
  Require(buffers.at(125).front()->GetWaypoint()->GetLaneId() == target, "request not assigned to requested lane");
  for (int i = 0; i < 5; ++i) {
    motion.location += transform.GetForwardVector() * (speed * 0.02f);
    state.UpdateKinematicState(125, motion);
    stage.Update(0);
    Require(buffers.at(125).front()->GetWaypoint()->GetLaneId() == target,
            "lane-change target lost on subsequent native cycle");
  }
  // Place the observed vehicle at its target center, then request the opposite
  // lane without waiting for the random-change distance cooldown.
  auto reached = buffers.at(125).front()->GetWaypoint()->GetTransform();
  motion.location = reached.location;
  motion.rotation = reached.rotation;
  state.UpdateKinematicState(125, motion);
  ctm::ParametersTestPeer::Force(parameters, 125, !right);
  stage.Update(0);
  Require(buffers.at(125).front()->GetWaypoint()->GetLaneId() == -2,
          "new explicit request swallowed by random-change cooldown");
  stage.RemoveActor(125);
  stage.Reset();
}

void RunExit(const ctm::LocalMapPtr &map, int road, int exit_road, bool cancel,
             bool through = false, bool late_signal = false, bool near_fork = false) {
  std::vector<ctm::ActorId> ids{126};
  ctm::BufferMap buffers;
  ctm::SimulationState state;
  ctm::TrackTraffic traffic;
  ctm::Parameters parameters;
  std::vector<ctm::ActorId> removed;
  ctm::LocalizationFrame output(1);
  ctm::RandomGenerator random(42);
  auto transform = map->GetMap().GetWaypointXODR(road, late_signal ? -3 : -4, 10.0f)->GetTransform();
  ctm::KinematicState motion{transform.location, transform.rotation,
                           transform.GetForwardVector() * 25.0f, 90.0f, true, false, {}};
  state.AddActor(126, motion, {ctm::ActorType::Vehicle, 2.4f, 0.9f, 0.8f},
                 {carla::rpc::TrafficLightState::Green, false});
  ctm::ParametersTestPeer::NoRandom(parameters, 126);
  ctm::LocalizationStage stage(ids, buffers, state, traffic, map, parameters, removed, output, random);
  stage.Update(0);
  ctm::ParametersTestPeer::ExitRoute(parameters, 126, false, through);
  stage.Update(0);
  if (late_signal && !near_fork) {
    ctm::ParametersTestPeer::Force(parameters, 126, true);
    stage.Update(0);
    Require(buffers.at(126).front()->GetWaypoint()->GetLaneId() == -4,
            "mainline plan suppressed late ordinary right signal");
  }
  if (cancel) {
    ctm::ParametersTestPeer::ExitRoute(parameters, 126, true);
    stage.Update(0);
    Require(!parameters.GetUploadRoute(126), "empty route cancellation not consumed");
    Require(parameters.GetImportedRoute(126).empty(), "exit intent retained after cancel");
  }
  bool reached = false;
  bool reached_through = false;
  bool near_signal_sent = false;
  bool through_cleanup = false;
  for (int cycle = 0; cycle < 180; ++cycle) {
    const bool fork_buffered = std::any_of(buffers.at(126).begin(), buffers.at(126).end(),
      [road](const ctm::SimpleWaypointPtr &wp) {
        return wp->GetWaypoint()->GetRoadId() == (road == 39 ? 1184 : 774);
      });
    if (near_fork && !near_signal_sent && fork_buffered) {
      ctm::ParametersTestPeer::Force(parameters, 126, true);
      near_signal_sent = true;
    }
    stage.Update(0);
    Require(removed.empty(), "exit route deleted vehicle");
    auto &buffer = buffers.at(126);
    Require(buffer.size() > 1, "exit buffer exhausted");
    auto target = buffer.at(1)->GetWaypoint();
    reached = reached || target->GetRoadId() == exit_road;
    reached_through = reached_through || target->GetRoadId() == (road == 39 ? 1184 : 774);
    if (through && reached_through && !through_cleanup) {
      // The Python observer clears the one-shot plan after physical passage.
      ctm::ParametersTestPeer::ExitRoute(parameters, 126, true);
      through_cleanup = true;
    }
    auto next = target->GetTransform();
    motion.location = next.location;
    motion.rotation = next.rotation;
    motion.velocity = next.GetForwardVector() * 25.0f;
    state.UpdateKinematicState(126, motion);
  }
  Require(cancel || (through ? !reached && reached_through : reached),
          "route did not select the requested exit/mainline branch");
  Require(!near_fork || near_signal_sent, "near-fork signal was not exercised");
  Require(parameters.GetImportedRoute(126).empty(), "one-shot route not consumed");
  stage.RemoveActor(126);
  stage.Reset();
}

int main(int argc, char **argv) {
  try {
    Require(argc == 2, "Town04 path required");
    std::ifstream file(argv[1]);
    Require(file.good(), "Town04 missing");
    std::string xodr((std::istreambuf_iterator<char>(file)), {});
    auto world = carla::MakeShared<carla::client::Map>("Town04", xodr);
    auto map = std::make_shared<ctm::InMemoryMap>(world);
    map->SetUp();
    unsigned count = 0;
    for (float speed : {25.0f, 15.35f, 33.3f, 5.0f})
      for (bool right : {false, true})
        for (float offset : {0.0f, 0.5f, 2.0f, 4.5f}) {
          Run(map, speed, right, offset);
          ++count;
        }
    std::cout << "PASS " << count << " real LocalizationStage cases; both directions, "
              << "target retention and immediate next request\n";
    for (bool cancel : {false, true}) {
      RunExit(map, 39, 1191, cancel);
      RunExit(map, 47, 782, cancel);
    }
    std::cout << "PASS 4 real Town04 automatic exit/cancellation cases\n";
    for (bool late_signal : {false, true}) {
      RunExit(map, 39, 1191, false, true, late_signal);
      RunExit(map, 47, 782, false, true, late_signal);
    }
    std::cout << "PASS 4 real Town04 mainline cases, including late right signals\n";
    RunExit(map, 39, 1191, false, true, true, true);
    RunExit(map, 47, 782, false, true, true, true);
    std::cout << "PASS 2 real Town04 late signals after branch prebuffering\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "FAIL: " << error.what() << '\n';
    return 1;
  }
}
