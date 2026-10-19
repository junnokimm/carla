// Real production LocalizationStage + offline Town04. No Client, RPC or simulator.
#include "carla/client/Map.h"
#include "carla/trafficmanager/LocalizationStage.h"
#include "carla/trafficmanager/Constants.h"
#include <fstream>
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
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "FAIL: " << error.what() << '\n';
    return 1;
  }
}
